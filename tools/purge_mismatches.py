import os
import csv
import sys
import io
import shutil
import subprocess
from pathlib import Path

# Fix encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CSV_BLUEPRINT, MUSIC_DIR
import argparse

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = Path(CSV_BLUEPRINT)
LOG_PATH = Path(os.path.join(ROOT_DIR, "logs", "mismatches.txt"))
LYRICS_DIR = Path(os.path.join(ROOT_DIR, "configs", "lyrics"))

def get_rclone_path():
    import platform
    import shutil
    if platform.system() == "Windows":
        path = os.path.join(ROOT_DIR, "rclone.exe")
        if os.path.exists(path):
            return path
    resolved = shutil.which("rclone")
    if resolved:
        return resolved
    return "rclone"

RCLONE_EXE = get_rclone_path()

def purge_mismatches(dry_run=True):
    print("=" * 60)
    print(f" FMP AUTO-SCRUBBER: PURGING MISMATCHED TRACKS (Dry-Run: {dry_run})")
    print("=" * 60)

    if not LOG_PATH.exists():
        print(f"[FATAL] Mismatches log not found at {LOG_PATH}")
        return

    # 1. Parse the text file
    paths_to_delete = []
    with open(LOG_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            if "Path: " in line:
                path = line.split("Path: ")[1].strip()
                if path:
                    paths_to_delete.append(Path(path))

    if not paths_to_delete:
        print("[!] No paths found to delete.")
        return

    print(f"[*] Found {len(paths_to_delete)} corrupted files to purge.")

    # 2. Backup CSV
    backup_csv = CSV_PATH.with_name(CSV_PATH.name + ".purge_backup")
    shutil.copy2(CSV_PATH, backup_csv)
    print(f"[*] Backed up database to {backup_csv.name}")

    # 3. Read CSV
    rows = []
    fieldnames = []
    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)

    purged_count = 0

    for idx, g_path in enumerate(paths_to_delete):
        filename = g_path.name
        track_name = g_path.stem
        folder = g_path.parent.name
        
        print(f"\n[{idx+1}/{len(paths_to_delete)}] Purging: {track_name}")

        # A. Delete from CSV (matches by filename stem, not the CSV's own
        # Track Name field - these frequently differ, e.g. a "(Clean)" suffix
        # or feat. formatting, so this can legitimately miss the intended row;
        # that's reported below, not silently assumed correct)
        matched_rows = [r for r in rows if r.get('Track Name') == track_name]
        if matched_rows:
            print(f"  [{'DRY-RUN: would remove' if dry_run else 'OK'}] {'Would remove' if dry_run else 'Removed'} from CSV database.")
            if not dry_run:
                rows = [r for r in rows if r.get('Track Name') != track_name]
        else:
            print("  [-] Not found in CSV database (filename stem didn't match any Track Name).")

        # B. Delete from G: Drive
        if g_path.exists():
            if dry_run:
                print(f"  [DRY-RUN] Would delete from local G: Drive: {g_path}")
            else:
                try:
                    g_path.unlink()
                    print("  [✓] Deleted from local G: Drive.")
                except Exception as e:
                    print(f"  [-] Failed to delete from G: Drive: {e}")

        # C. Delete Lyrics
        lyric_file = LYRICS_DIR / f"{track_name}.txt"
        if lyric_file.exists():
            if dry_run:
                print(f"  [DRY-RUN] Would delete lyrics file: {lyric_file.name}")
            else:
                try:
                    lyric_file.unlink()
                    print("  [✓] Deleted Lyrics text file.")
                except:
                    pass

        # D. Delete from Citrus3 FTP (Remote)
        remote_path = f"citrus3:/{folder}/{filename}"
        if dry_run:
            print(f"  [DRY-RUN] Would delete from Citrus3 FTP: {remote_path}")
        else:
            try:
                res = subprocess.run([RCLONE_EXE, "deletefile", remote_path], capture_output=True, text=True)
                if res.returncode == 0:
                    print("  [✓] Deleted from Citrus3 FTP server.")
                else:
                    print(f"  [-] Failed to delete from Citrus3 FTP: {res.stderr.strip()}")
            except Exception as e:
                print(f"  [-] Error executing rclone: {e}")

        purged_count += 1

    # 4. Save CSV
    if dry_run:
        print(f"\n[DRY-RUN] Would save CSV database with {len(rows)} remaining records.")
    else:
        with open(CSV_PATH, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    print("\n" + "=" * 60)
    print(f" PURGE COMPLETE! Successfully vaporized {purged_count} corrupted tracks.")
    print("=" * 60)

    # 5. Git commit if auto push is enabled
    if dry_run:
        print("[DRY-RUN] Would commit and push CSV updates to Git.")
        return
    sys.path.append(r"c:\FMP_Ultimate")
    try:
        from config import AUTO_GIT_PUSH, git_operation_lock, git_safe_pull
        if AUTO_GIT_PUSH:
            print("[*] Committing CSV updates to Git...")
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            with git_operation_lock(timeout=60):
                res_branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True)
                branch_name = res_branch.stdout.strip()
                # Pathspec-restricted commit + real branch detection instead of a
                # hardcoded "origin main" - same bug fixed elsewhere tonight.
                subprocess.run(["git", "add", "configs/fmp_data_7718.csv"], cwd=repo_root, check=True, capture_output=True)
                subprocess.run(["git", "commit", "configs/fmp_data_7718.csv", "-m", f"Auto-Scrubber: Purged {purged_count} corrupted mismatched tracks"], cwd=repo_root, check=True, capture_output=True)
                # Merge, not rebase+autostash - see config.git_safe_pull's
                # docstring for the 2026-09-03 incident this replaces.
                git_safe_pull(branch_name, cwd=repo_root)
                subprocess.run(["git", "push", "origin", branch_name], cwd=repo_root, check=True, capture_output=True)
            print("[✓] Pushed changes successfully to GitHub.")
    except Exception as e:
        print(f"[-] Git push failed or skipped: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Purge FMP corrupted/mismatched tracks")
    parser.add_argument("--live", action="store_true", help="Actually delete files and update the database (default is a dry run)")
    args = parser.parse_args()
    purge_mismatches(dry_run=not args.live)
