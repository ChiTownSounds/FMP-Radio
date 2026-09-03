#!/usr/bin/env python3
"""
FMP Ultimate - Explicit Track Renaming Engine
================================================================================
Retroactively strips '(Explicit)' suffixes and renames files/database records:
1. Updatesconfigs/fmp_data_7718.csv.
2. Updates local SQLite library.
3. Updates remote VM SQLite library via SSH.
4. Renames local G: Drive files.
5. Renames remote VM files via SSH.
"""

import os
import csv
import sys
import sqlite3
import re
import argparse
import subprocess
import time

# File configurations
CSV_PATH = "C:/FMP_Ultimate/configs/fmp_data_7718.csv"
LOCAL_DB = "C:/FMP_Broadcaster/fmp_radio.db"
G_DRIVE_ROOT = "G:/My Drive/FMP MUSIC/BASE/MUSIC"

def clean_track_name(name):
    # Strip (Explicit), [Explicit], (Dirty), (Uncut) case-insensitively
    cleaned_name = re.sub(r'\s*\((Explicit|Explicit Version|Dirty|Uncut)\)', '', name, flags=re.IGNORECASE)
    cleaned_name = re.sub(r'\s*\[(Explicit|Explicit Version|Dirty|Uncut)\]', '', cleaned_name, flags=re.IGNORECASE)
    return cleaned_name.strip()

def clean_file_path(path):
    # Strips suffix from filename portion of path
    directory, filename = os.path.split(path)
    base_name, ext = os.path.splitext(filename)
    cleaned_base = clean_track_name(base_name)
    return os.path.join(directory, cleaned_base + ext).replace('\\', '/')

def update_local_db(old_path, new_path, new_title):
    if not os.path.exists(LOCAL_DB):
        print(f"  [-] Local database not found at {LOCAL_DB}. Skipping local DB update.")
        return False
    try:
        conn = sqlite3.connect(LOCAL_DB)
        c = conn.cursor()
        c.execute("SELECT id FROM media_library WHERE file_path = ?", (new_path,))
        exists = c.fetchone()
        if exists:
            c.execute("DELETE FROM media_library WHERE file_path = ?", (old_path,))
            print(f"  [OK] Target path already exists. Deleted local duplicate entry '{old_path}'.")
        else:
            c.execute(
                "UPDATE media_library SET file_path = ?, title = ? WHERE file_path = ?",
                (new_path, new_title, old_path)
            )
            print("  [OK] Updated local database entry.")
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"  [-] Local database update error: {e}")
        return False

def update_remote_db_and_files(old_path, new_path, new_title, old_filename, new_filename):
    # SSH execution command for remote database and file rename
    # Remote root path is assumed to be /home/ubuntu/music
    # Values are embedded via !r (repr()), not raw f-string interpolation -
    # the previous version wrapped each value in plain '{value}' with no
    # escaping at all, so a track title/path containing an apostrophe (a
    # real, not-hypothetical case - e.g. "Sleepy Brown - I Can't Wait")
    # would break out of the string literal in this generated script, which
    # is piped straight to `python3` on the VM. repr() safely escapes
    # quotes/backslashes/unicode so the generated script stays valid Python
    # regardless of what's in these values.
    remote_script = f"""import sqlite3, os
db_path = '/home/ubuntu/FMP-Broadcaster/fmp_radio.db'
music_root = '/home/ubuntu/music'

# Update DB
conn = sqlite3.connect(db_path)
c = conn.cursor()
c.execute("SELECT id FROM media_library WHERE file_path = ?", ({new_path!r},))
exists = c.fetchone()
if exists:
    c.execute("DELETE FROM media_library WHERE file_path = ?", ({old_path!r},))
    db_msg = "Deleted remote duplicate entry"
else:
    c.execute("UPDATE media_library SET file_path = ?, title = ? WHERE file_path = ?", ({new_path!r}, {new_title!r}, {old_path!r}))
    db_msg = "Updated remote database"
conn.commit()
conn.close()
print("  [OK] " + db_msg)

# Find and rename physical file on VM
for root, dirs, files in os.walk(music_root):
    for f in files:
        if f == {old_filename!r}:
            src = os.path.join(root, f)
            dst = os.path.join(root, {new_filename!r})
            if os.path.exists(dst):
                os.remove(src)
                print(f"  [OK] Deleted duplicate file on VM: {{src}}")
            else:
                os.rename(src, dst)
                print(f"  [OK] Renamed file on VM: {{src}} -> {{dst}}")
"""
    try:
        # Run remote python script via SSH
        cmd = [
            "ssh", "-i", "C:/Users/chito/.ssh/id_ed25519", "ubuntu@ultimate.fmpmediagroup.com",
            "python3"
        ]
        result = subprocess.run(cmd, input=remote_script, capture_output=True, text=True, encoding='utf-8', check=True)
        print(result.stdout.strip())
        return True
    except Exception as e:
        safe_err = str(e).encode('ascii', errors='replace').decode('ascii')
        print(f"  [-] Remote SSH update error: {safe_err}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Explicit track name and file purging/renaming tool.")
    parser.add_argument("--live", action="store_true", help="Execute changes live (default is dry run)")
    args = parser.parse_args()

    dry_run = not args.live
    print("==================================================")
    print(f"       FMP ULTIMATE - EXPLICIT RENAMING TOOL     ")
    print(f"                 Mode: {'DRY RUN' if dry_run else 'LIVE'}                 ")
    print("==================================================")

    if not os.path.exists(CSV_PATH):
        print(f"[Error] Catalog CSV not found at {CSV_PATH}")
        sys.exit(1)

    # Read CSV rows
    rows = []
    fieldnames = []
    with open(CSV_PATH, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    tracks_to_rename = []
    for row in rows:
        track_name = row.get('Track Name', '')
        file_path = row.get('File Path', '')
        
        # Leave Maxwell as (uncut) per user instruction
        if "maxwell" in track_name.lower() and "uncut" in track_name.lower():
            continue
            
        # Check if name contains explicit indicators
        if any(suffix in track_name.lower() for suffix in ['(explicit)', '[explicit]', '(dirty)', '(uncut)']):
            tracks_to_rename.append(row)

    print(f"[*] Found {len(tracks_to_rename)} explicit tracks in the CSV database.")
    print("--------------------------------------------------")

    if not tracks_to_rename:
        print("[OK] No tracks found with explicit suffixes. Exiting.")
        sys.exit(0)

    rename_count = 0
    for idx, r in enumerate(tracks_to_rename, 1):
        old_name = r['Track Name']
        old_path = r['File Path']
        
        new_name = clean_track_name(old_name)
        new_path = clean_file_path(old_path)
        
        # Get filenames for renaming on disk
        _, old_filename = os.path.split(old_path)
        _, new_filename = os.path.split(new_path)

        # Skip if nothing would change
        if old_name == new_name and old_path == new_path:
            continue

        safe_old_name = old_name.encode('ascii', errors='replace').decode('ascii')
        safe_new_name = new_name.encode('ascii', errors='replace').decode('ascii')
        safe_old_path = old_path.encode('ascii', errors='replace').decode('ascii')
        safe_new_path = new_path.encode('ascii', errors='replace').decode('ascii')
        print(f"[{idx}/{len(tracks_to_rename)}] Target: '{safe_old_name}'")
        print(f"  -> New Name: '{safe_new_name}'")
        print(f"  -> Old Path: '{safe_old_path}'")
        print(f"  -> New Path: '{safe_new_path}'")

        if dry_run:
            print("  [DRY RUN] Would update database records & rename physical files.")
            rename_count += 1
            continue

        # Live renaming execution
        # 1. Update CSV row fields in-memory
        r['Track Name'] = new_name
        r['File Path'] = new_path

        # 2. Rename physical file locally on Google Drive
        # Resolve physical G Drive path (do not strip Music/ as G Drive root contains it)
        g_old_full = os.path.join(G_DRIVE_ROOT, old_path).replace('\\', '/')
        g_new_full = os.path.join(G_DRIVE_ROOT, new_path).replace('\\', '/')

        g_drive_success = False
        if os.path.exists(g_old_full):
            try:
                # Create destination folders if needed
                os.makedirs(os.path.dirname(g_new_full), exist_ok=True)
                if os.path.exists(g_new_full):
                    os.remove(g_old_full)
                    print(f"  [OK] Deleted duplicate file on G: Drive: {g_old_full}")
                else:
                    os.rename(g_old_full, g_new_full)
                    print(f"  [OK] Renamed file on G: Drive: {g_old_full} -> {g_new_full}")
                g_drive_success = True
            except Exception as e:
                print(f"  [-] G: Drive file rename error: {e}")
        else:
            print(f"  [-] Local file not found on G: Drive: {g_old_full}")
            # Still proceed to allow DB cleaning even if file is unsynced

        # 3. Update local SQLite database
        update_local_db(old_path, new_path, new_name)

        # 4. Update remote VM SQLite database and rename remote file
        update_remote_db_and_files(old_path, new_path, new_name, old_filename, new_filename)
        
        rename_count += 1
        time.sleep(0.5)

    # Save CSV updates if live
    if not dry_run and rename_count > 0:
        print("--------------------------------------------------")
        print(f"[*] Saving master CSV database update...")
        try:
            temp_path = CSV_PATH + ".tmp"
            with open(temp_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            os.replace(temp_path, CSV_PATH)
            print("[OK] Successfully wrote master CSV catalog updates!")
        except Exception as e:
            print(f"[Error] Failed to save updated CSV: {e}")
            sys.exit(1)

    print("==================================================")
    print(f"          RENAMING COMPLETE ({rename_count} processed)         ")
    print("==================================================")

if __name__ == "__main__":
    main()
