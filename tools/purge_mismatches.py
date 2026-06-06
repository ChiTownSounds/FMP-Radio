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

CSV_PATH = Path(r"c:\FMP_Ultimate\configs\fmp_data_7718.csv")
LOG_PATH = Path(r"c:\FMP_Ultimate\logs\mismatches.txt")
LYRICS_DIR = Path(r"c:\FMP_Ultimate\configs\lyrics")
RCLONE_EXE = r"c:\FMP_Ultimate\rclone.exe"

def purge_mismatches():
    print("=" * 60)
    print(" FMP AUTO-SCRUBBER: PURGING MISMATCHED TRACKS")
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

        # A. Delete from CSV
        original_len = len(rows)
        rows = [r for r in rows if r.get('Track Name') != track_name]
        if len(rows) < original_len:
            print("  [✓] Removed from CSV database.")
        else:
            print("  [-] Not found in CSV database.")

        # B. Delete from G: Drive
        if g_path.exists():
            try:
                g_path.unlink()
                print("  [✓] Deleted from local G: Drive.")
            except Exception as e:
                print(f"  [-] Failed to delete from G: Drive: {e}")

        # C. Delete Lyrics
        lyric_file = LYRICS_DIR / f"{track_name}.txt"
        if lyric_file.exists():
            try:
                lyric_file.unlink()
                print("  [✓] Deleted Lyrics text file.")
            except:
                pass

        # D. Delete from Citrus3 FTP (Remote)
        remote_path = f"citrus3:/{folder}/{filename}"
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
    with open(CSV_PATH, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print("\n" + "=" * 60)
    print(f" PURGE COMPLETE! Successfully vaporized {purged_count} corrupted tracks.")
    print("=" * 60)

    # 5. Git commit if auto push is enabled
    sys.path.append(r"c:\FMP_Ultimate")
    try:
        from config import AUTO_GIT_PUSH
        if AUTO_GIT_PUSH:
            print("[*] Committing CSV updates to Git...")
            subprocess.run(["git", "add", str(CSV_PATH)], cwd=r"c:\FMP_Ultimate", check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"Auto-Scrubber: Purged {purged_count} corrupted mismatched tracks"], cwd=r"c:\FMP_Ultimate", check=True, capture_output=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=r"c:\FMP_Ultimate", check=True, capture_output=True)
            print("[✓] Pushed changes successfully to GitHub.")
    except Exception as e:
        print(f"[-] Git push failed or skipped: {e}")


if __name__ == "__main__":
    purge_mismatches()
