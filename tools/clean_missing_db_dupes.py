import os
import sys
import re
import csv
import io
import shutil
import subprocess
from pathlib import Path

# Fix encoding for Windows consoles
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CSV_BLUEPRINT, AUTO_GIT_PUSH, MUSIC_DIR
import argparse

G_DRIVE_MUSIC = MUSIC_DIR
# This script previously had no safety gate at all despite writing the CSV
# and pushing to git unconditionally, using only a weak word-overlap title
# match (no file-size or content verification) to decide what to remove.
# Defaults to a dry run; pass --live to actually write/push.
DRY_RUN = True

def titles_are_similar(t1, t2):
    # Normalize to alphanumeric lowercase words
    w1 = set(re.findall(r'[a-z0-9]+', t1.lower()))
    w2 = set(re.findall(r'[a-z0-9]+', t2.lower()))
    # Remove common short words
    stop_words = {'the', 'a', 'an', 'and', 'or', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}
    w1 = w1 - stop_words
    w2 = w2 - stop_words
    # Check if there is any overlap
    return len(w1.intersection(w2)) > 0

def get_absolute_gpath(file_path_on_server):
    clean_rel = file_path_on_server.replace('\\', '/')
    if clean_rel.upper().startswith('Z:/'):
        clean_rel = clean_rel[3:]
    elif clean_rel.lower().startswith('/home/ubuntu/music/'):
        clean_rel = clean_rel[len('/home/ubuntu/music/'):]
    return os.path.join(G_DRIVE_MUSIC, clean_rel.replace('/', os.sep))

def main():
    print("=" * 80)
    print(" FMP DATABASE CLEANUP FOR PRE-DELETED DUPLICATE SONGS")
    print("=" * 80)

    if not os.path.exists(CSV_BLUEPRINT):
        print("CSV Database not found.")
        return

    # 1. Read CSV
    rows = []
    fieldnames = []
    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)

    print(f"[*] Loaded {len(rows)} rows from database.")

    # 2. Separate present and missing rows
    present_by_artist = {}
    missing_items = []
    
    for idx, row in enumerate(rows):
        track_name = row.get('Track Name', '').strip()
        if not track_name:
            continue
            
        gpath = get_absolute_gpath(row.get('File Path', ''))
        artist = track_name.split(' - ')[0] if ' - ' in track_name else ''
        artist_clean = re.split(r'\s+(feat\.?|featuring|with|w/|f/|and|&)\s+', artist.lower())[0]
        artist_clean = re.sub(r'[^a-z0-9]', '', artist_clean)
        
        # We exclude Danny Boy from deduplication
        if "danny boy" in track_name.lower():
            continue

        if os.path.exists(gpath):
            if artist_clean not in present_by_artist:
                present_by_artist[artist_clean] = []
            present_by_artist[artist_clean].append((idx, row))
        else:
            # Skip Ne-Yo sex with my ex which is intentionally missing but not a duplicate
            if "ne-yo - sex with my ex" not in track_name.lower():
                missing_items.append((idx, row, artist_clean))

    # 3. Match missing items with present duplicates from the same artist
    indices_to_remove = set()
    print("\n[*] Scanning missing entries for present duplicates...")
    for idx, row, artist_clean in missing_items:
        track_name = row.get('Track Name', '')
        title = track_name.split(' - ', 1)[-1] if ' - ' in track_name else track_name
        
        # Check if we have present files for this artist
        p_list = present_by_artist.get(artist_clean, [])
        for p_idx, p_row in p_list:
            p_track = p_row.get('Track Name', '')
            p_title = p_track.split(' - ', 1)[-1] if ' - ' in p_track else p_track
            
            if titles_are_similar(title, p_title):
                print(f"  [DUPLICATE DETECTED] Removing missing db row:")
                print(f"    - Missing: '{track_name}'")
                print(f"    - Kept Present: '{p_track}'")
                indices_to_remove.add(idx)
                break

    if not indices_to_remove:
        print("[OK] No missing duplicate rows found to clean up in the database!")
        return

    if DRY_RUN:
        print(f"\n[DRY-RUN] Would remove {len(indices_to_remove)} rows from the database. Pass --live to actually apply this.")
        return

    # 4. Write CSV
    print(f"\n[*] Writing updated database (removing {len(indices_to_remove)} rows)...")
    remaining_rows = [row for idx, row in enumerate(rows) if idx not in indices_to_remove]
    
    # Backup CSV first
    csv_path = Path(CSV_BLUEPRINT)
    backup_csv = csv_path.with_name(csv_path.name + ".cleanup_backup")
    try:
        shutil.copy2(CSV_BLUEPRINT, backup_csv)
        print(f"  [OK] Backed up CSV to {backup_csv.name}")
    except Exception as e:
        print(f"  [FAIL] Backup failed: {e}")

    try:
        with open(CSV_BLUEPRINT, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(remaining_rows)
        print("  [OK] Database CSV updated successfully.")
    except Exception as e:
        print(f"  [ERROR] Failed to write CSV: {e}")
        return

    # 5. Git Commit & Push
    if AUTO_GIT_PUSH:
        try:
            print("\n[*] Committing database updates to Git...")
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            res_branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True)
            branch_name = res_branch.stdout.strip()
            # Pathspec-restricted commit + real branch detection instead of a
            # hardcoded "origin main" - same bug fixed elsewhere tonight.
            subprocess.run(["git", "add", "configs/fmp_data_7718.csv"], cwd=repo_root, check=True, capture_output=True)
            msg = f"Auto-Cleanup: Purged {len(indices_to_remove)} duplicate database rows for deleted files"
            subprocess.run(["git", "commit", "configs/fmp_data_7718.csv", "-m", msg], cwd=repo_root, check=True, capture_output=True)
            subprocess.run(["git", "pull", "--rebase", "--autostash", "origin", branch_name], cwd=repo_root, check=True, capture_output=True)
            subprocess.run(["git", "push", "origin", branch_name], cwd=repo_root, check=True, capture_output=True)
            print("  [OK] Pushed changes successfully to GitHub.")
        except Exception as e:
            print(f"  [-] Git push failed: {e}")

    print("\n" + "=" * 80)
    print("CLEANUP COMPLETE.")
    print("=" * 80)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Clean up CSV rows for pre-deleted duplicate songs")
    parser.add_argument("--live", action="store_true", help="Actually write the CSV and push (default is a dry run)")
    args = parser.parse_args()
    if args.live:
        DRY_RUN = False
    main()
