import os
import csv
import sys
import io
import shutil
import subprocess
import argparse
from pathlib import Path

# Fix encoding for Windows consoles
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.append(r"c:\FMP_Ultimate")
from config import CSV_BLUEPRINT, AUTO_GIT_PUSH
CSV_BLUEPRINT = Path(CSV_BLUEPRINT)

REPORT_PATH = Path(r"c:\FMP_Ultimate\logs\final_audio_mismatches.txt")
G_DRIVE_MUSIC = Path(r"G:\My Drive\FMP MUSIC\BASE\MUSIC")
G_DRIVE_QUARANTINE = Path(r"G:\My Drive\FMP MUSIC\BASE\Quarantine")
LYRICS_DIR = Path(r"c:\FMP_Ultimate\configs\lyrics")
RCLONE_EXE = r"c:\FMP_Ultimate\rclone.exe"

def parse_mismatches():
    if not REPORT_PATH.exists():
        print(f"[ERROR] Mismatch report not found at {REPORT_PATH}")
        return []

    targets = []
    current_expected = None
    current_actual = None

    with open(REPORT_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith("Expected: "):
                current_expected = line.split("Expected: ", 1)[1].strip()
            elif line.startswith("Actual: "):
                current_actual = line.split("Actual: ", 1)[1].strip()
            elif line.startswith("Path: ") and current_expected:
                path_str = line.split("Path: ", 1)[1].strip()
                targets.append({
                    "expected": current_expected,
                    "actual": current_actual or "Unknown",
                    "path": Path(path_str)
                })
                current_expected = None
                current_actual = None

    return targets

def main():
    parser = argparse.ArgumentParser(description="Quarantine FMP Audio Mismatches")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run without moving or deleting anything")
    args = parser.parse_args()

    print("=" * 70)
    print(f" FMP AUDIO MISMATCH QUARANTINE MANAGER (Dry-Run: {args.dry_run})")
    print("=" * 70)

    targets = parse_mismatches()
    if not targets:
        print("[-] No targets found to quarantine.")
        return

    print(f"[*] Parsed {len(targets)} candidate tracks from report.")

    # 1. Filter out Danny Boy tracks
    quarantine_list = []
    skipped_count = 0
    for t in targets:
        is_danny_boy = (
            "danny boy" in t["expected"].lower() or 
            "danny boy" in t["actual"].lower() or 
            "danny boy" in t["path"].name.lower()
        )
        if is_danny_boy:
            print(f"[SKIP] Explicitly skipping Danny Boy track: {t['expected']}")
            skipped_count += 1
        else:
            quarantine_list.append(t)

    print(f"[*] Processing {len(quarantine_list)} tracks (Skipped: {skipped_count}).")

    if not quarantine_list:
        print("[!] No tracks remaining to quarantine.")
        return

    # 2. Dry run print or perform file moves
    moved_count = 0
    failed_moves = 0
    csv_removals = []
    
    # We will read CSV once to perform DB updates in memory
    db_rows = []
    db_fieldnames = []
    if CSV_BLUEPRINT.exists():
        with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            db_fieldnames = reader.fieldnames
            for row in reader:
                db_rows.append(row)

    print("\n--- Processing Tracks ---")
    for idx, item in enumerate(quarantine_list):
        src_path = item["path"]
        expected_title = item["expected"]
        
        # Calculate relative path from G_DRIVE_MUSIC to preserve structure
        try:
            rel_path = src_path.relative_to(G_DRIVE_MUSIC)
            dest_path = G_DRIVE_QUARANTINE / rel_path
        except ValueError:
            # Fallback if path is somehow outside of G_DRIVE_MUSIC
            rel_path = Path(src_path.parent.name) / src_path.name
            dest_path = G_DRIVE_QUARANTINE / rel_path

        print(f"\n[{idx+1}/{len(quarantine_list)}] Expected: {expected_title}")
        print(f"  Source:      {src_path}")
        print(f"  Destination: {dest_path}")

        # A. Local File Move
        if src_path.exists():
            if args.dry_run:
                print(f"  [DRY-RUN] Would move local file to: {dest_path}")
                moved_count += 1
            else:
                try:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src_path), str(dest_path))
                    print("  [✓] Moved local file to Quarantine.")
                    moved_count += 1
                except Exception as e:
                    print(f"  [✗] Failed to move file: {e}")
                    failed_moves += 1
        else:
            print("  [!] Local file does not exist (already moved or missing).")
            # We still proceed to scrub CSV and remote server just in case

        # B. Remote Server Scrub (Citrus3 FTP)
        # rel_path contains `<Era>/<Filename.mp3>` which maps to citrus3:/<Era>/<Filename.mp3>
        remote_path = f"citrus3:/{rel_path.as_posix()}"
        if args.dry_run:
            print(f"  [DRY-RUN] Would execute rclone to delete remote file: {remote_path}")
        else:
            try:
                res = subprocess.run([RCLONE_EXE, "deletefile", remote_path], capture_output=True, text=True)
                if res.returncode == 0:
                    print("  [✓] Deleted from Citrus3 FTP server.")
                else:
                    err_msg = res.stderr.strip() or f"exit code {res.returncode}"
                    print(f"  [-] Citrus3 FTP deletion returned: {err_msg}")
            except Exception as e:
                print(f"  [-] Error executing rclone: {e}")

        # C. Lyrics Deletion
        safe_track_name = "".join(c for c in expected_title if c not in r'\/:*?"<>|').strip()
        lyric_file = LYRICS_DIR / f"{safe_track_name}.txt"
        if lyric_file.exists():
            if args.dry_run:
                print(f"  [DRY-RUN] Would delete lyrics file: {lyric_file.name}")
            else:
                try:
                    lyric_file.unlink()
                    print("  [✓] Deleted Lyrics text file.")
                except Exception as e:
                    print(f"  [-] Failed to delete lyrics file: {e}")

        # D. Record for CSV removal
        csv_removals.append(expected_title)

    # 3. Save CSV changes if not dry-run
    if csv_removals and db_rows:
        original_db_len = len(db_rows)
        # Filter rows
        filtered_rows = [
            r for r in db_rows 
            if r.get('Track Name') not in csv_removals
        ]
        removed_db_count = original_db_len - len(filtered_rows)

        if args.dry_run:
            print(f"\n[DRY-RUN] Would remove {removed_db_count} rows from CSV database.")
        else:
            print(f"\n[*] Removing {removed_db_count} matching rows from CSV database...")
            # Backup CSV first
            backup_csv = CSV_BLUEPRINT.with_name(CSV_BLUEPRINT.name + ".quarantine_backup")
            try:
                shutil.copy2(CSV_BLUEPRINT, backup_csv)
                print(f"[✓] Backed up CSV database to {backup_csv.name}")
            except Exception as e:
                print(f"[!] Warning: CSV backup failed: {e}")

            # Write updated CSV
            try:
                with open(CSV_BLUEPRINT, 'w', encoding='utf-8', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=db_fieldnames)
                    writer.writeheader()
                    writer.writerows(filtered_rows)
                print("[✓] Updated CSV database written successfully.")
            except Exception as e:
                print(f"[ERROR] Failed to write updated CSV: {e}")

            # Git Commit & Push
            if AUTO_GIT_PUSH:
                try:
                    print("[*] Committing CSV updates to Git...")
                    subprocess.run(["git", "add", str(CSV_BLUEPRINT)], cwd=r"c:\FMP_Ultimate", check=True, capture_output=True)
                    msg = f"Auto-Scrubber: Quarantined {moved_count} audio-mismatched tracks"
                    subprocess.run(["git", "commit", "-m", msg], cwd=r"c:\FMP_Ultimate", check=True, capture_output=True)
                    subprocess.run(["git", "push", "origin", "main"], cwd=r"c:\FMP_Ultimate", check=True, capture_output=True)
                    print("[✓] Pushed changes successfully to GitHub.")
                except Exception as e:
                    print(f"[-] Git push failed or skipped: {e}")

    print("\n" + "=" * 70)
    if args.dry_run:
        print(f"DRY-RUN COMPLETE. Would quarantine {moved_count} tracks.")
    else:
        print(f"QUARANTINE COMPLETE. Successfully quarantined {moved_count} tracks (Failed: {failed_moves}).")
    print("=" * 70)

if __name__ == "__main__":
    main()
