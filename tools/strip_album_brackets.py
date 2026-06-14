import os
import csv
import sys
import io
import re
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

G_DRIVE_MUSIC = Path(r"G:\My Drive\FMP MUSIC\BASE\MUSIC")
LYRICS_DIR = Path(r"c:\FMP_Ultimate\configs\lyrics")
RCLONE_EXE = r"c:\FMP_Ultimate\rclone.exe"

def clean_track_name(name):
    # Strip bracketed album details
    return re.sub(r'\s*\[.*?\]', '', name).strip()

def clean_path_brackets(path_str):
    # Strip brackets from file names in path
    if not path_str:
        return ""
    # We only want to strip brackets from the filename itself, not the directory
    parts = path_str.replace('\\', '/').split('/')
    filename = parts[-1]
    cleaned_filename = re.sub(r'\s*\[.*?\]', '', filename).strip()
    parts[-1] = cleaned_filename
    return "/".join(parts)

def get_absolute_gpath(file_path_on_server):
    # Map Z:/ relative path to local G: drive path
    clean_rel_path = file_path_on_server.replace('\\', '/')
    if clean_rel_path.upper().startswith('Z:/'):
        clean_rel_path = clean_rel_path[3:]
    return G_DRIVE_MUSIC / clean_rel_path.replace('/', os.sep)

def main():
    parser = argparse.ArgumentParser(description="Strip Album Brackets from FMP library")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run without modifying anything")
    args = parser.parse_args()

    print("=" * 75)
    print(f" FMP ALBUM BRACKET REMOVAL MIGRATION TOOL (Dry-Run: {args.dry_run})")
    print("=" * 75)

    if not CSV_BLUEPRINT.exists():
        print(f"[FATAL] CSV Database not found at {CSV_BLUEPRINT}")
        return

    # 1. Read the CSV Database
    rows = []
    fieldnames = []
    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)

    print(f"[*] Loaded {len(rows)} tracks from database.")

    # We will keep track of target filenames to avoid collisions
    # both with files that already exist on disk and with other files we plan to rename.
    planned_target_paths = set()
    # Populate existing files that won't be changed
    for row in rows:
        track_name = row.get('Track Name', '').strip()
        file_path = row.get('File Path', '').strip()
        if track_name and '[' not in track_name:
            planned_target_paths.add(file_path.lower())

    renamed_count = 0
    collision_count = 0
    missing_local_count = 0
    modified_rows = []
    rename_items = []

    print("\n--- Scanning and Renaming Local Files ---")

    for idx, row in enumerate(rows):
        track_name = row.get('Track Name', '').strip()
        file_path = row.get('File Path', '').strip()

        if not track_name or '[' not in track_name:
            # Keep row unchanged
            modified_rows.append(row)
            continue

        # A. Calculate New Names
        new_track_name = clean_track_name(track_name)
        new_file_path = clean_path_brackets(file_path)

        # B. Check for Collisions
        # If the target path is already planned or exists, append a suffix
        temp_new_path = new_file_path
        suffix_idx = 1
        while temp_new_path.lower() in planned_target_paths:
            # Append suffix to name before .mp3
            base, ext = os.path.splitext(new_file_path)
            temp_new_path = f"{base} - Alternate{ext}"
            new_track_name = f"{clean_track_name(track_name)} - Alternate"
            suffix_idx += 1
        
        if temp_new_path != new_file_path:
            new_file_path = temp_new_path
            collision_count += 1
            print(f"[COLLISION RESOLVED] '{track_name}' -> '{new_track_name}'")

        planned_target_paths.add(new_file_path.lower())

        # C. Map Paths
        src_gpath = get_absolute_gpath(file_path)
        dest_gpath = get_absolute_gpath(new_file_path)

        # Determine remote FTP paths
        def get_ftp_path(z_path):
            clean_rel = z_path.replace('\\', '/')
            if clean_rel.upper().startswith('Z:/'):
                clean_rel = clean_rel[3:]
            return f"citrus3:/{clean_rel}"

        src_ftp = get_ftp_path(file_path)
        dest_ftp = get_ftp_path(new_file_path)

        already_renamed = False

        if args.dry_run:
            # Dry run operations
            if src_gpath.exists():
                print(f"    [DRY-RUN] Would move local G: file to: {dest_gpath}")
            print(f"    [DRY-RUN] Would execute rclone: rclone moveto \"{src_ftp}\" \"{dest_ftp}\"")
            
            # Lyrics check
            safe_old_track = "".join(c for c in track_name if c not in r'\/:*?"<>|').strip()
            safe_new_track = "".join(c for c in new_track_name if c not in r'\/:*?"<>|').strip()
            lyric_old_file = LYRICS_DIR / f"{safe_old_track}.txt"
            lyric_new_file = LYRICS_DIR / f"{safe_new_track}.txt"
            if lyric_old_file.exists():
                print(f"    [DRY-RUN] Would rename lyrics file: {lyric_old_file.name} -> {lyric_new_file.name}")
        else:
            # Active run operations
            # 1. Rename G: Drive local file
            if src_gpath.exists():
                try:
                    dest_gpath.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src_gpath), str(dest_gpath))
                except Exception as e:
                    print(f"    [✗] Failed to rename local file for '{track_name}': {e}")
            elif dest_gpath.exists():
                # Already renamed in previous run
                already_renamed = True
            else:
                missing_local_count += 1
                print(f"    [!] Warning: Local file missing for track: '{track_name}'")

            # 2. Rename Lyrics file
            safe_old_track = "".join(c for c in track_name if c not in r'\/:*?"<>|').strip()
            safe_new_track = "".join(c for c in new_track_name if c not in r'\/:*?"<>|').strip()
            lyric_old_file = LYRICS_DIR / f"{safe_old_track}.txt"
            lyric_new_file = LYRICS_DIR / f"{safe_new_track}.txt"
            if lyric_old_file.exists():
                try:
                    shutil.move(str(lyric_old_file), str(lyric_new_file))
                except Exception as e:
                    print(f"    [-] Failed to rename lyrics file: {e}")

            # Queue for FTP execution
            rename_items.append({
                'index': renamed_count + 1,
                'track_name': track_name,
                'src_ftp': src_ftp,
                'dest_ftp': dest_ftp,
                'already_renamed': already_renamed
            })

        # Update database fields in row
        new_row = row.copy()
        new_row['Track Name'] = new_track_name
        new_row['File Path'] = new_file_path
        modified_rows.append(new_row)
        renamed_count += 1

    # 3. Parallel Remote FTP Moves (Active Run only)
    if rename_items and not args.dry_run:
        import concurrent.futures
        import threading

        print(f"\n[*] Starting remote FTP renames for {len(rename_items)} items using 12 concurrent workers...")
        
        print_lock = threading.Lock()
        completed_ftp = 0

        def run_ftp_rename(item):
            nonlocal completed_ftp
            src_ftp = item['src_ftp']
            dest_ftp = item['dest_ftp']
            track_name = item['track_name']
            
            if item.get('already_renamed', False):
                with print_lock:
                    completed_ftp += 1
                    if completed_ftp % 100 == 0 or completed_ftp == len(rename_items):
                        print(f"[*] FTP Progress: {completed_ftp}/{len(rename_items)} tracks processed (skipped already renamed).")
                return

            status_str = ""
            try:
                res = subprocess.run([RCLONE_EXE, "moveto", src_ftp, dest_ftp], capture_output=True, text=True, timeout=15)
                if res.returncode == 0:
                    status_str = f"    [✓] [{item['index']}] Remote FTP renamed: '{track_name}'"
                else:
                    err = res.stderr.strip() or f"exit code {res.returncode}"
                    if "Source doesn't exist" in err or "directory not found" in err:
                        status_str = f"    [-] [{item['index']}] Remote FTP already renamed or missing: '{track_name}'"
                    else:
                        status_str = f"    [-] [{item['index']}] Remote FTP rename returned error: {err}"
            except subprocess.TimeoutExpired:
                status_str = f"    [!] [{item['index']}] Remote FTP rename timed out after 15s: '{track_name}'"
            except Exception as e:
                status_str = f"    [-] [{item['index']}] Remote FTP rename error: {e} for '{track_name}'"
                
            with print_lock:
                completed_ftp += 1
                if completed_ftp % 20 == 0 or completed_ftp == len(rename_items):
                    print(f"[*] FTP Progress: {completed_ftp}/{len(rename_items)} tracks processed.")
                # Print errors, timeouts, or specific warnings to stdout
                if "error" in status_str.lower() or "timed out" in status_str.lower() or "Warning" in status_str:
                    print(status_str)

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            executor.map(run_ftp_rename, rename_items)
            
        print(f"[✓] Parallel remote FTP renames complete. {completed_ftp} items processed.")

    # 5. Write CSV database changes back
    if renamed_count > 0 and not args.dry_run:
        print(f"\n[*] Writing database updates for {renamed_count} rows...")
        # Backup CSV first
        backup_csv = CSV_BLUEPRINT.with_name(CSV_BLUEPRINT.name + ".rename_backup")
        try:
            shutil.copy2(CSV_BLUEPRINT, backup_csv)
            print(f"[✓] Backed up CSV database to {backup_csv.name}")
        except Exception as e:
            print(f"[!] Warning: CSV backup failed: {e}")

        # Write updated CSV
        try:
            with open(CSV_BLUEPRINT, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(modified_rows)
            print("[✓] Updated CSV database written successfully.")
        except Exception as e:
            print(f"[ERROR] Failed to write updated CSV: {e}")

        # Git Commit & Push
        if AUTO_GIT_PUSH:
            try:
                print("[*] Committing CSV updates to Git...")
                subprocess.run(["git", "add", str(CSV_BLUEPRINT)], cwd=r"c:\FMP_Ultimate", check=True, capture_output=True)
                msg = f"Auto-Rename: Stripped album brackets from {renamed_count} tracks"
                subprocess.run(["git", "commit", "-m", msg], cwd=r"c:\FMP_Ultimate", check=True, capture_output=True)
                subprocess.run(["git", "push", "origin", "main"], cwd=r"c:\FMP_Ultimate", check=True, capture_output=True)
                print("[✓] Pushed changes successfully to GitHub.")
            except Exception as e:
                print(f"[-] Git push failed or skipped: {e}")

    print("\n" + "=" * 75)
    if args.dry_run:
        print(f"DRY-RUN COMPLETE. Would rename {renamed_count} tracks (Collisions: {collision_count}, Missing local: {missing_local_count}).")
    else:
        print(f"MIGRATION COMPLETE. Successfully renamed {renamed_count} tracks (Collisions resolved: {collision_count}, Missing local: {missing_local_count}).")
    print("=" * 75)

if __name__ == "__main__":
    main()
