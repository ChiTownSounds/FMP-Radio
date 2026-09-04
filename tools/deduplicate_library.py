import os
import sys
import re
import csv
import io
import shutil
import subprocess
from collections import defaultdict

# Fix encoding for Windows consoles
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Ensure the root dir is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CSV_BLUEPRINT, AUTO_GIT_PUSH, MUSIC_DIR, git_operation_lock, git_safe_pull
from modules.fingerprint_compare import compare_fingerprints
from mutagen.id3 import ID3

# Minimum acoustid-style similarity score [0,1] required for PASS 0 to treat
# two same-titled rows as confirmed audio duplicates. Same default used by
# tools/fingerprint_dedup.py's report-only scan, which showed zero false
# positives at this threshold against the real library (2026-09-03).
FINGERPRINT_MATCH_THRESHOLD = 0.90

def get_rclone_path():
    import platform
    import shutil
    if platform.system() == "Windows":
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rclone.exe")
        if os.path.exists(path):
            return path
    resolved = shutil.which("rclone")
    if resolved:
        return resolved
    return "rclone"

RCLONE_EXE = get_rclone_path()
G_DRIVE_MUSIC = MUSIC_DIR
LYRICS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "configs", "lyrics")

# Set to True to only report duplicates without deleting anything
DRY_RUN = True

def titles_are_similar(t1, t2):
    # Normalize them to alphanumeric lowercase words
    w1 = set(re.findall(r'[a-z0-9]+', t1.lower()))
    w2 = set(re.findall(r'[a-z0-9]+', t2.lower()))
    # Remove common short words
    stop_words = {'the', 'a', 'an', 'and', 'or', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}
    w1 = w1 - stop_words
    w2 = w2 - stop_words
    # Check if there is any overlap
    return len(w1.intersection(w2)) > 0

def normalize_track_key(track_name):
    if not track_name:
        return ""
    parts = track_name.split(' - ', 1)
    if len(parts) == 2:
        artist, title = parts
    else:
        artist = ""
        title = track_name
        
    # Clean artist: remove features, only keep first artist
    artist_clean = artist.lower()
    artist_clean = re.split(r'\s+(feat\.?|featuring|with|w/|f/|and|&)\s+', artist_clean)[0]
    artist_clean = re.sub(r'[^a-z0-9]', '', artist_clean)
    
    # Clean title:
    title_clean = title.lower()
    # 1. Remove bracketed album names
    title_clean = re.sub(r'\[.*?\]', '', title_clean)
    # 2. Only remove parenthesized features, keep other parentheses (like Live, Remix, etc.)
    title_clean = re.sub(r'\((?:feat|featuring|f/)\.?\s+.*?\)', '', title_clean)
    # 3. Remove non-alphanumeric characters
    title_clean = re.sub(r'[^a-z0-9]', '', title_clean)
    
    return f"{artist_clean}_{title_clean}"

def get_absolute_gpath(file_path_on_server):
    clean_rel = file_path_on_server.replace('\\', '/')
    if clean_rel.upper().startswith('Z:/'):
        clean_rel = clean_rel[3:]
    elif clean_rel.lower().startswith('/home/ubuntu/music/'):
        clean_rel = clean_rel[len('/home/ubuntu/music/'):]
    return os.path.join(G_DRIVE_MUSIC, clean_rel.replace('/', os.sep))

def get_fingerprint(row, g_path):
    """CSV 'Fingerprint' column first (backfilled 2026-09-03); falls back to
    the file's own TXXX:AUDIO_FINGERPRINT tag in case the CSV cell lags
    behind it. Same pattern as tools/fingerprint_dedup.py's get_fingerprint()."""
    fp = row.get('Fingerprint', '').strip()
    if fp:
        return fp
    try:
        id3 = ID3(g_path)
        key = 'TXXX:AUDIO_FINGERPRINT'
        if key in id3 and id3[key].text:
            return str(id3[key].text[0]).strip() or None
    except Exception:
        pass
    return None


def get_ftp_path(z_path):
    clean_rel = z_path.replace('\\', '/')
    if clean_rel.upper().startswith('Z:/'):
        clean_rel = clean_rel[3:]
    elif clean_rel.lower().startswith('/home/ubuntu/music/'):
        clean_rel = clean_rel[len('/home/ubuntu/music/'):]
    return f"citrus3:/{clean_rel}"

def evaluate_row_quality(row):
    # Score a row's quality based on metadata presence and bitrate
    score = 0
    
    # Has Youtube source URL
    if row.get('Source_URL', '').strip():
        score += 10
        
    # Bitrate quality
    bitrate_str = row.get('Bitrate', '0').lower()
    try:
        bitrate = int(''.join(filter(str.isdigit, bitrate_str)))
        if bitrate >= 320:
            score += 5
        elif bitrate >= 256:
            score += 3
        elif bitrate >= 192:
            score += 1
    except:
        pass
        
    # Lyrics present
    lyrics = row.get('Lyrics', '').strip().lower()
    if lyrics and lyrics not in ['unknown', 'none', 'false']:
        score += 3
        
    # bpm and duration
    try:
        if int(row.get('bpm', 0)) > 0:
            score += 1
    except:
        pass
        
    return score

def main():
    print("=" * 80)
    print(f" FMP LIBRARY DEDUPLICATION UTILITY (DRY_RUN: {DRY_RUN})")
    print("=" * 80)

    if not os.path.exists(CSV_BLUEPRINT):
        print(f"[ERROR] Database CSV not found at {CSV_BLUEPRINT}")
        return

    # 1. Read CSV
    rows = []
    fieldnames = []
    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)

    print(f"[*] Loaded {len(rows)} tracks from database.")

    indices_to_delete = set()
    files_to_delete_local = []
    files_to_delete_remote = []
    lyrics_to_delete = []

    # ----------------------------------------------------
    # PASS 0: FINGERPRINT-CONFIRMED EXACT-TITLE DUPLICATES
    # ----------------------------------------------------
    # Highest-confidence signal in this file: rows whose Track Name string
    # is byte-for-byte identical AND whose Chromaprint audio fingerprint
    # (backfilled 2026-09-03 - see modules/fingerprint_compare.py) confirms
    # the audio itself matches, not just the title. Runs before PASS 1/2 so
    # they can skip anything already resolved here instead of re-scoring it
    # independently under a different signal. Deliberately does NOT touch
    # Clean/Explicit/Radio Edit version pairs - those have different Track
    # Name strings on purpose (a station needs both for different dayparts,
    # FCC compliance) and are exactly the class of "duplicate" this pass
    # must never delete.
    print("\n--- PASS 0: Scanning for Fingerprint-Confirmed Exact-Title Duplicates ---")
    exact_title_groups = defaultdict(list)
    for idx, row in enumerate(rows):
        track_name = row.get('Track Name', '').strip()
        if not track_name:
            continue
        exact_title_groups[track_name].append((idx, row))

    fp_confirmed_groups = 0
    fp_mismatch_groups = 0
    for track_name, items in exact_title_groups.items():
        if len(items) < 2:
            continue

        # Only rows with a real file on disk and a usable fingerprint are
        # eligible for this pass.
        eligible = []
        for idx, row in items:
            gpath = get_absolute_gpath(row.get('File Path', ''))
            if not os.path.exists(gpath):
                continue
            fingerprint = get_fingerprint(row, gpath)
            if not fingerprint:
                continue
            eligible.append((idx, row, gpath, fingerprint))

        if len(eligible) < 2:
            continue

        # Pick the highest-quality row as the reference; cluster every
        # other row that fingerprint-matches it above threshold. A row with
        # the identical title but audio that does NOT match its reference
        # is left untouched and reported separately - that's a mislabeled
        # or corrupted file, not a duplicate, and needs a human to look.
        scored = sorted(eligible, key=lambda t: (evaluate_row_quality(t[1]), -t[0]), reverse=True)
        ref_row, ref_gpath, ref_fp = scored[0][1], scored[0][2], scored[0][3]

        cluster = [scored[0]]
        outliers = []
        for candidate in scored[1:]:
            score = compare_fingerprints(ref_fp, candidate[3])
            if score >= FINGERPRINT_MATCH_THRESHOLD:
                cluster.append(candidate)
            else:
                outliers.append(candidate)

        if len(cluster) >= 2:
            fp_confirmed_groups += 1
            print(f"  [FINGERPRINT-CONFIRMED DUPES] '{track_name}'")
            print(f"    [KEEP] Score: {evaluate_row_quality(ref_row)} - {ref_gpath}")
            for idx, row, gpath, fingerprint in cluster[1:]:
                indices_to_delete.add(idx)
                if gpath == ref_gpath:
                    # Same physical file as the row being kept - this is a
                    # duplicate DATABASE ROW, not a duplicate file. The kept
                    # row still needs this exact file, so only the redundant
                    # CSV entry is removed; the file and its Citrus3 FTP copy
                    # are left alone. Mirrors PASS 1's existing
                    # already-cleaned-up-duplicate handling above.
                    print(f"    [DELETE ROW ONLY - same file as KEEP] Score: {evaluate_row_quality(row)} - {gpath}")
                else:
                    print(f"    [DELETE] Score: {evaluate_row_quality(row)} - {gpath}")
                    files_to_delete_local.append(gpath)
                    files_to_delete_remote.append(get_ftp_path(row.get('File Path', '')))
                    safe_track = "".join(c for c in track_name if c not in r'\/:*?"<>|').strip()
                    lyric_file = os.path.join(LYRICS_DIR, f"{safe_track}.txt")
                    if os.path.exists(lyric_file):
                        lyrics_to_delete.append(lyric_file)

        if outliers:
            fp_mismatch_groups += 1
            print(f"    [TITLE MATCH, AUDIO MISMATCH - NOT touched] '{track_name}': "
                  f"{len(outliers)} row(s) share this exact title but audio does not match")

    print(f"[*] PASS 0: {fp_confirmed_groups} fingerprint-confirmed duplicate group(s) queued, "
          f"{fp_mismatch_groups} same-title group(s) with mismatched audio left for manual review.")

    # ----------------------------------------------------
    # PASS 1: Deduplicate by IDENTICAL LOCAL FILE SIZE or ALREADY DELETED DUPES
    # ----------------------------------------------------
    print("\n--- PASS 1: Scanning for Identical Audio File Sizes ---")
    size_groups = defaultdict(list)
    missing_dupes = []

    # Map normalized keys to whether they have a present file on disk
    key_has_present = {}
    for idx, row in enumerate(rows):
        track_name = row.get('Track Name', '').strip()
        if not track_name:
            continue
        key = normalize_track_key(track_name)
        gpath = get_absolute_gpath(row.get('File Path', ''))
        if os.path.exists(gpath):
            key_has_present[key] = True

    for idx, row in enumerate(rows):
        # Already resolved by PASS 0's stronger fingerprint-confirmed signal -
        # don't let this pass's weaker size-based heuristic re-score or
        # re-queue the same row under a different (possibly conflicting) verdict.
        if idx in indices_to_delete:
            continue

        track_name = row.get('Track Name', '').strip()
        if not track_name:
            continue

        gpath = get_absolute_gpath(row.get('File Path', ''))
        key = normalize_track_key(track_name)

        if os.path.exists(gpath):
            size = os.path.getsize(gpath)
            # Group by size and normalized artist to ensure they are the same artist
            artist = track_name.split(' - ')[0] if ' - ' in track_name else ''
            artist_clean = re.split(r'\s+(feat\.?|featuring|with|w/|f/|and|&)\s+', artist.lower())[0]
            artist_clean = re.sub(r'[^a-z0-9]', '', artist_clean)
            
            size_groups[(size, artist_clean)].append((idx, row))
        else:
            # If the file does not exist, but there is a present file for this key,
            # this is a duplicate row whose file we already deleted!
            if key_has_present.get(key, False):
                missing_dupes.append((idx, row))

    # Add already-deleted duplicate rows
    if missing_dupes:
        print(f"[*] Found {len(missing_dupes)} duplicate database entries whose files were already cleaned up:")
        for idx, row in missing_dupes:
            print(f"    - Flagging for database removal: '{row['Track Name']}'")
            indices_to_delete.add(idx)

    # Identical file size + same artist is a strong signal on its own, but a
    # false size collision between two different same-artist tracks of equal
    # duration is real for fixed-bitrate CBR encoding (used throughout this
    # library). titles_are_similar() is the secondary guard against that -
    # but it's the same weak word-overlap heuristic hardened elsewhere
    # tonight, so only an EXACT title match auto-deletes here; a merely
    # "similar" title (same size + same artist, but not the same title) is
    # reported for manual review instead, matching the pattern already
    # applied to clean_missing_db_dupes.py and this file's own PASS 2.
    size_dupe_groups = {}
    size_needs_review = []
    for (size, artist), items in size_groups.items():
        if len(items) > 1:
            first_title = items[0][1].get('Track Name', '').split(' - ', 1)[-1]
            exact_items = [items[0]]
            for idx, row in items[1:]:
                row_title = row.get('Track Name', '').split(' - ', 1)[-1]
                if row_title.strip().lower() == first_title.strip().lower():
                    exact_items.append((idx, row))
                elif titles_are_similar(first_title, row_title):
                    size_needs_review.append((items[0][1].get('Track Name', ''), row.get('Track Name', '')))
            if len(exact_items) > 1:
                size_dupe_groups[(size, artist)] = exact_items

    print(f"[*] Found {len(size_dupe_groups)} duplicate size groups.")
    if size_needs_review:
        print(f"[*] {len(size_needs_review)} same-size/same-artist pair(s) need manual review (title-word-overlap only, not auto-deleted):")
        for a, b in size_needs_review:
            print(f"    - '{a}' vs '{b}'")

    for (size, artist), items in size_dupe_groups.items():
        print(f"  [SIZE DUPES: {size / (1024*1024):.2f} MB, Artist: '{artist}']")
        
        # Score and sort
        scored_items = []
        for idx, row in items:
            score = evaluate_row_quality(row)
            scored_items.append((score, -idx, idx, row))
        scored_items.sort(reverse=True)
        
        # Keep best
        best_score, _, best_idx, best_row = scored_items[0]
        print(f"    [KEEP] '{best_row['Track Name']}' (Score: {best_score})")
        
        # Delete others
        for score, _, idx, row in scored_items[1:]:
            print(f"    [DELETE] '{row['Track Name']}' (Score: {score})")
            indices_to_delete.add(idx)
            
            # Queue paths
            gpath = get_absolute_gpath(row.get('File Path', ''))
            if os.path.exists(gpath):
                files_to_delete_local.append(gpath)
            
            ftp_path = get_ftp_path(row.get('File Path', ''))
            files_to_delete_remote.append(ftp_path)
            
            safe_track = "".join(c for c in row.get('Track Name', '') if c not in r'\/:*?"<>|').strip()
            lyric_file = os.path.join(LYRICS_DIR, f"{safe_track}.txt")
            if os.path.exists(lyric_file):
                lyrics_to_delete.append(lyric_file)

    # ----------------------------------------------------
    # PASS 2: Deduplicate by SAFER NORMALIZED KEY
    # ----------------------------------------------------
    print("\n--- PASS 2: Scanning for Same Song Title (Safer Key) ---")
    key_groups = defaultdict(list)
    for idx, row in enumerate(rows):
        # Skip if already marked for deletion in Pass 1
        if idx in indices_to_delete:
            continue
            
        track_name = row.get('Track Name', '').strip()
        if not track_name:
            continue

        key = normalize_track_key(track_name)
        if key:
            key_groups[key].append((idx, row))

    title_dupe_groups = {k: v for k, v in key_groups.items() if len(v) > 1}
    print(f"[*] Found {len(title_dupe_groups)} duplicate title groups.")

    for key, items in title_dupe_groups.items():
        print(f"  [TITLE DUPES: {key}]")
        
        scored_items = []
        for idx, row in items:
            score = evaluate_row_quality(row)
            gpath = get_absolute_gpath(row.get('File Path', ''))
            exists_local = os.path.exists(gpath)
            scored_items.append((score, exists_local, -idx, idx, row))
        scored_items.sort(reverse=True)
        
        best_score, best_exists, _, best_idx, best_row = scored_items[0]
        print(f"    [KEEP] '{best_row['Track Name']}' (Score: {best_score}, Exists: {best_exists})")

        # PASS 2 matches on normalized title/artist key alone - no file-size or
        # audio-content verification like PASS 1 has. That's too weak a signal
        # to auto-delete on: a title-key collision can be a genuine duplicate,
        # or it can be two different recordings (different cut, different
        # bitrate re-encode, or truly different songs) that just normalize to
        # the same key. Report only; these need a human to actually listen/
        # compare before anything gets deleted.
        for score, exists, _, idx, row in scored_items[1:]:
            print(f"    [REVIEW NEEDED] '{row['Track Name']}' (Score: {score}, Exists: {exists}) - title-key match only, not auto-queued for deletion")

    print("\n" + "=" * 80)
    print(" CLEANUP SUMMARY")
    print("=" * 80)
    print(f" Database entries to remove:  {len(indices_to_delete)}")
    print(f" Local G: Drive files to delete: {len(files_to_delete_local)}")
    print(f" Remote FTP files to delete:  {len(files_to_delete_remote)}")
    print(f" Lyrics files to delete:      {len(lyrics_to_delete)}")
    print("=" * 80)

    if DRY_RUN:
        print("[!] DRY_RUN is True. No files have been deleted, and the database was not modified.")
        print("[!] To execute the cleanup, edit this script and set DRY_RUN = False.")
        return

    # 4. Perform Deletion
    # A. Delete local files
    print("\n[*] Deleting local files on G: Drive...")
    for fpath in files_to_delete_local:
        try:
            os.remove(fpath)
            print(f"  [OK] Deleted: {os.path.basename(fpath)}")
        except Exception as e:
            print(f"  [FAIL] Failed to delete local file: {fpath} ({e})")

    # B. Delete lyrics files
    print("\n[*] Deleting lyrics files...")
    for fpath in lyrics_to_delete:
        try:
            os.remove(fpath)
            print(f"  [OK] Deleted lyrics: {os.path.basename(fpath)}")
        except Exception as e:
            print(f"  [FAIL] Failed to delete lyrics: {fpath} ({e})")

    # C. Delete remote FTP files in parallel using rclone deletefile
    print("\n[*] Deleting remote files on Citrus3 FTP in parallel...")
    import concurrent.futures
    
    def delete_remote_file(ftp_path):
        try:
            # Check if file exists by running rclone ls first, or just run deletefile
            res = subprocess.run([RCLONE_EXE, "deletefile", ftp_path], capture_output=True, text=True, timeout=15)
            if res.returncode == 0:
                print(f"  [OK] Deleted remote FTP file: {ftp_path}")
            else:
                err = res.stderr.strip() or f"exit code {res.returncode}"
                print(f"  [-] FTP delete returned: {err} (Proceeding anyway)")
        except subprocess.TimeoutExpired:
            print(f"  [!] FTP delete timed out after 15s: {ftp_path}")
        except Exception as e:
            print(f"  [-] FTP delete error: {e} for {ftp_path}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        executor.map(delete_remote_file, files_to_delete_remote)

    # D. Write updated CSV
    print("\n[*] Updating local CSV database...")
    remaining_rows = [row for idx, row in enumerate(rows) if idx not in indices_to_delete]
    
    # Backup CSV first
    from pathlib import Path
    csv_path = Path(CSV_BLUEPRINT)
    backup_csv = csv_path.with_name(csv_path.name + ".dedupe_backup")
    try:
        shutil.copy2(CSV_BLUEPRINT, backup_csv)
        print(f"  [OK] Backed up CSV database to {backup_csv.name}")
    except Exception as e:
        print(f"  [!] Warning: CSV backup failed: {e}")

    try:
        with open(CSV_BLUEPRINT, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(remaining_rows)
        print("  [OK] Updated CSV database written successfully.")
    except Exception as e:
        print(f"  [ERROR] Failed to write updated CSV: {e}")

    # E. Git Commit & Push
    if AUTO_GIT_PUSH:
        try:
            print("\n[*] Committing database updates to Git...")
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            with git_operation_lock(timeout=60):
                res_branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True)
                branch_name = res_branch.stdout.strip()
                # Pathspec-restricted commit (can't sweep up unrelated staged files),
                # matching the same fix applied everywhere else in this codebase
                # tonight. This previously hardcoded "origin main" regardless of
                # the actual current branch (which has been "dev" all night) and
                # used a bare `git commit -m` with no pathspec.
                subprocess.run(["git", "add", "configs/fmp_data_7718.csv"], cwd=repo_root, check=True, capture_output=True)
                msg = f"Auto-Dedupe: Cleaned up {len(indices_to_delete)} duplicate songs"
                subprocess.run(["git", "commit", "configs/fmp_data_7718.csv", "-m", msg], cwd=repo_root, check=True, capture_output=True)
                # Merge, not rebase+autostash - see config.git_safe_pull's
                # docstring for the 2026-09-03 incident this replaces.
                git_safe_pull(branch_name, cwd=repo_root)
                subprocess.run(["git", "push", "origin", branch_name], cwd=repo_root, check=True, capture_output=True)
            print("  [OK] Pushed changes successfully to GitHub.")
        except Exception as e:
            print(f"  [-] Git push failed or skipped: {e}")

    print("\n" + "=" * 80)
    print(f"CLEANUP COMPLETE. Removed {len(indices_to_delete)} duplicate rows from database.")
    print("=" * 80)

if __name__ == "__main__":
    main()
