import os
import sys
import re
import csv
import io
import subprocess
from collections import defaultdict

# Fix encoding for Windows consoles
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Ensure the root dir is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CSV_BLUEPRINT, AUTO_GIT_PUSH

RCLONE_EXE = r"c:\FMP_Ultimate\rclone.exe"

def normalize_title(title):
    # Convert to lowercase
    s = title.lower()
    # Remove bracketed and parenthesized info (e.g. [Album], (feat. ...), (Radio Edit))
    s = re.sub(r'\[.*?\]', '', s)
    s = re.sub(r'\(.*?\)', '', s)
    # Remove common featured separators and everything after
    s = re.split(r'\s+(feat\.?|featuring|with|w/|f/|and|&)\s+', s)[0]
    # Remove non-alphanumeric characters
    s = re.sub(r'[^a-z0-9]', '', s)
    return s.strip()

def main():
    print("=" * 80)
    print(" FMP SERVER PEEK & DEDUPLICATION ANALYZER")
    print("=" * 80)

    if not os.path.exists(CSV_BLUEPRINT):
        print(f"[ERROR] Database CSV not found at {CSV_BLUEPRINT}")
        return

    # 1. Read the CSV Database
    csv_tracks = []
    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            csv_tracks.append(row)
    
    print(f"[*] Loaded {len(csv_tracks)} tracks from local database CSV.")

    # 2. Get remote file listing with size details via rclone lsl
    print("[*] Querying Citrus3 FTP server for full file list with sizes...")
    try:
        res = subprocess.run([RCLONE_EXE, "lsl", "citrus3:/"], capture_output=True, text=True, check=True, encoding='utf-8')
        lines = res.stdout.splitlines()
    except Exception as e:
        print(f"[ERROR] Failed to query Citrus3 FTP server: {e}")
        return

    # Parse rclone lsl output
    # Format of each line: "  123456 2026-06-13 19:30:25.000000000 Folder/File.mp3"
    remote_files = []
    for line in lines:
        line = line.strip()
        if not line or not line.lower().endswith('.mp3'):
            continue
        parts = re.split(r'\s+', line, maxsplit=3)
        if len(parts) >= 4:
            try:
                size = int(parts[0])
                rel_path = parts[3]
                remote_files.append({
                    'size': size,
                    'path': rel_path,
                    'filename': rel_path.split('/')[-1]
                })
            except ValueError:
                pass

    print(f"[*] Found {len(remote_files)} MP3 files on Citrus3 FTP.")

    # ANALYSIS 1: Duplicate Entries in CSV by exact File Path
    path_to_rows = defaultdict(list)
    for row in csv_tracks:
        path = row.get('File Path', '').replace('\\', '/').lower()
        if path.startswith('z:/'):
            path = path[3:]
        path_to_rows[path].append(row)

    csv_path_dupes = {p: rows for p, rows in path_to_rows.items() if len(rows) > 1}
    print(f"\n[1] Duplicate File Paths in Database CSV: {len(csv_path_dupes)}")
    for p, rows in list(csv_path_dupes.items())[:10]:
        print(f"    - Path: '{p}' (Referenced {len(rows)} times)")
        for r in rows:
            print(f"        * Track Name: '{r.get('Track Name')}'")

    # ANALYSIS 2: Duplicate Files on FTP by exact File Size
    size_to_files = defaultdict(list)
    for rf in remote_files:
        size_to_files[rf['size']].append(rf)

    ftp_size_dupes = {size: files for size, files in size_to_files.items() if len(files) > 1 and size > 1024 * 1024} # > 1MB
    print(f"\n[2] Exact Duplicate Audio Files on Citrus3 FTP (by identical size in bytes): {len(ftp_size_dupes)}")
    for size, files in list(ftp_size_dupes.items())[:10]:
        print(f"    - Size: {size / (1024*1024):.2f} MB")
        for f in files:
            print(f"        * '{f['path']}'")

    # ANALYSIS 3: Orphaned Files on FTP (not referenced in CSV)
    csv_paths_set = set(path_to_rows.keys())
    orphaned_ftp_files = []
    for rf in remote_files:
        norm_path = rf['path'].lower()
        if norm_path not in csv_paths_set:
            orphaned_ftp_files.append(rf)

    print(f"\n[3] Orphaned Files on Citrus3 FTP (on server but NOT in CSV database): {len(orphaned_ftp_files)}")
    # Group orphaned files by folder
    orph_by_folder = defaultdict(list)
    for f in orphaned_ftp_files:
        folder = f['path'].split('/')[0] if '/' in f['path'] else 'Root'
        orph_by_folder[folder].append(f)
    
    for folder, files in orph_by_folder.items():
        print(f"    - Folder '{folder}': {len(files)} orphaned files")
        for f in files[:5]:
            print(f"        * '{f['path']}'")
        if len(files) > 5:
            print(f"        * ... and {len(files) - 5} more")

    # ANALYSIS 4: Normalized Song Name Duplicate Check (e.g. Same song different versions/folders/names)
    normalized_to_files = defaultdict(list)
    for rf in remote_files:
        # Get filename without .mp3
        name_no_ext = rf['filename'][:-4] if rf['filename'].lower().endswith('.mp3') else rf['filename']
        norm = normalize_title(name_no_ext)
        if norm:
            normalized_to_files[norm].append(rf)

    song_name_dupes = {norm: files for norm, files in normalized_to_files.items() if len(files) > 1}
    print(f"\n[4] Normalized Track Duplicates on FTP (same artist/title, potentially different files): {len(song_name_dupes)}")
    for norm, files in list(song_name_dupes.items())[:10]:
        print(f"    - Normalized Key: '{norm}'")
        for f in files:
            print(f"        * '{f['path']}' (Size: {f['size'] / (1024*1024):.2f} MB)")

    print("\n" + "=" * 80)
    print(" SUGGESTED ACTION PLAN FOR '3 OR 4 COPIES OF EVERY SONG'")
    print("=" * 80)
    if csv_path_dupes:
        print("[*] FIX 1: Clean up duplicate paths in CSV. We have database entries referencing the same file.")
    if orphaned_ftp_files:
        print("[*] FIX 2: Delete orphaned files on FTP. These are leftover uploads causing duplicates on the playout server.")
    if ftp_size_dupes:
        print("[*] FIX 3: Delete duplicate audio files. We have identical audio files uploaded with different names.")
    print("=" * 80)

if __name__ == "__main__":
    main()
