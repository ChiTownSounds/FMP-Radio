import os
import sys
import csv
import io
import shutil
import sqlite3
import subprocess
import urllib.request
import re
from pathlib import Path

# Fix console encoding for Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', write_through=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', write_through=True)

# Append root dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CSV_BLUEPRINT, AUTO_GIT_PUSH, is_non_song, git_operation_lock, git_safe_pull

try:
    from mutagen.mp3 import MP3
except ImportError:
    print("[*] Installing mutagen library for audio metadata parsing...")
    subprocess.run([sys.executable, "-m", "pip", "install", "mutagen"], check=True, capture_output=True)
    from mutagen.mp3 import MP3

from modules.tagger import AutoMaster

# --- CONFIGURATIONS ---
from config import MUSIC_DIR, BROADCASTER_DB
G_DRIVE_MUSIC = Path(MUSIC_DIR)
BROADCASTER_DB = Path(BROADCASTER_DB)

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
# This previously defaulted to live (DRY_RUN = False) with no CLI flag to
# override it at all - running the script with zero arguments rewrote the
# CSV, wrote directly to the Broadcaster SQLite DB, and auto-pushed to git.
# Defaults to a dry run now; pass --live to actually apply changes.
DRY_RUN = True
SKIP_FTP = True  # Set to True to bypass Citrus3 FTP operations for speed

def normalize_track_key(name: str, explicit_val=None) -> str:
    if not name:
        return ""
    name_lower = name.lower()
    
    is_radio = 'radio edit' in name_lower or 'radio version' in name_lower
    if explicit_val is not None:
        is_explicit = explicit_val in [True, 1, 'true', '1', 'True'] or ('explicit' in name_lower and 'clean' not in name_lower)
    else:
        is_explicit = 'explicit' in name_lower and 'clean' not in name_lower
        
    parts = name.split(' - ', 1)
    if len(parts) == 2:
        artist, title = parts
    else:
        artist = ""
        title = name
        
    artist_clean = artist.lower()
    artist_clean = re.split(r'\s+(feat\.?|featuring|with|w/|f/|and|&)\s+', artist_clean)[0]
    artist_clean = re.sub(r'[^a-z0-9]', '', artist_clean)
    
    title_clean = title.lower()
    title_clean = re.sub(r'\[.*?\]', '', title_clean)
    title_clean = re.sub(r'\((?:feat\.?|featuring|f/)\.?\s+.*?\)', '', title_clean)
    
    # Check original title for modifiers to avoid key collisions on variants
    variant_suffix = ""
    title_lower_orig = title.lower()
    if "unplugged" in title_lower_orig:
        variant_suffix = "_unplugged"
    elif "live" in title_lower_orig:
        variant_suffix = "_live"
    elif "acoustic" in title_lower_orig:
        variant_suffix = "_acoustic"
    elif "remix" in title_lower_orig:
        variant_suffix = "_remix"
        
    removals = ["radio edit", "single mix", "album version", "rerecorded", "clean", "explicit", "remix"]
    for r in removals:
        title_clean = title_clean.replace(r, "")
    title_clean = re.sub(r'[^a-z0-9]', '', title_clean)
    
    base_key = f"{artist_clean}_{title_clean}{variant_suffix}"
    if is_radio:
        return f"{base_key}_radioedit"
    elif is_explicit:
        return f"{base_key}_explicit"
    else:
        return f"{base_key}_clean"

def get_era_category(folder_name: str) -> str:
    folder_lower = folder_name.lower()
    if "classics" in folder_lower:
        return "Classics"
    elif "old school" in folder_lower:
        return "Old School"
    elif "throwbacks" in folder_lower:
        return "Throwbacks"
    elif "new school" in folder_lower:
        return "New School"
    elif "live" in folder_lower:
        return "Live"
    elif "shows" in folder_lower:
        return "Shows"
    return "Unassigned"

def get_pool_id_from_folder(folder_name: str) -> int:
    if not folder_name:
        return None
    folder_lower = folder_name.lower()
    if "new school" in folder_lower:
        return 1
    elif "classics" in folder_lower:
        return 2
    elif "throwbacks" in folder_lower:
        return 3
    elif "slow jam" in folder_lower or "quiet storm" in folder_lower:
        return 4
    elif "gospel" in folder_lower or "inspirational" in folder_lower:
        return 5
    elif "blues" in folder_lower:
        return 6
    elif "old school" in folder_lower:
        return 7
    elif "deep cut" in folder_lower:
        return 8
    return None

def get_relative_path(path: Path) -> str:
    # Get path relative to the local G_DRIVE_MUSIC root
    try:
        return path.relative_to(G_DRIVE_MUSIC).as_posix()
    except ValueError:
        return path.name

def run_sync():
    db_updated = False
    print("="*80)
    print(" FMP ULTIMATE - AUTOMATED LIBRARY & BROADCASTER DATABASE SYNC ENGINE")
    print("="*80)
    if DRY_RUN:
        print(" !!! RUNNING IN DRY_RUN MODE - NO PHYSICAL OR DATABASE CHANGES WILL OCCUR !!!")
        print("="*80)

    # 0. Git Pull to avoid collisions
    if not DRY_RUN:
        print("[*] Pulling latest updates from GitHub...")
        try:
            script_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            subprocess.run(["git", "pull", "--rebase"], check=True, capture_output=True, cwd=script_root)
            print("  [OK] Git pull complete.")
        except Exception as e:
            print(f"  [WARNING] Git pull failed: {e}")

    # 1. Scan local G: Drive
    print(f"\n[*] Scanning local G: Drive: {G_DRIVE_MUSIC}...")
    if not G_DRIVE_MUSIC.exists():
        print(f"[-] ERROR: G: Drive music directory not found at: {G_DRIVE_MUSIC}")
        print("Please check Google Drive mount and try again.")
        sys.exit(1)

    local_files = {}  # key: normalized_key -> value: {local_path, rel_path, filename_no_ext, folder}
    duplicate_files = []  # (key, kept_rel_path, discarded_rel_path) - same identity key, two physical files
    for root, dirs, files in os.walk(G_DRIVE_MUSIC):
        # Prune folders in-place to prevent scanning giant or unresponsive Google Drive directories
        for d in list(dirs):
            if d in ('Shows', '365 Commercials', 'STAGING'):
                dirs.remove(d)
        for file in files:
            if file.lower().endswith('.mp3'):
                filepath = Path(root) / file
                rel_path = get_relative_path(filepath)
                folder = rel_path.split('/')[0] if '/' in rel_path else ''
                filename_no_ext = filepath.stem
                key = normalize_track_key(filename_no_ext)
                if not key:
                    continue
                if key in local_files:
                    # Two physical files share the same identity key (e.g. a leftover
                    # "Song (Clean).mp3" alongside the canonical "Song.mp3" after a
                    # rename). Silently overwriting here used to make one of them
                    # invisible to the rest of this script - never matched to a CSV
                    # row, never imported, never flagged for cleanup. Keep whichever
                    # filename has no bracketed version tag (the canonical form per
                    # the "no version tags in filenames" convention); report the other
                    # as a duplicate instead of discarding it silently.
                    existing = local_files[key]
                    existing_has_tag = bool(re.search(r'\((?:clean|explicit|radio edit|radio version)\)', existing['filename_no_ext'], re.I))
                    new_has_tag = bool(re.search(r'\((?:clean|explicit|radio edit|radio version)\)', filename_no_ext, re.I))
                    if existing_has_tag and not new_has_tag:
                        duplicate_files.append((key, rel_path, existing['rel_path']))
                        local_files[key] = {'local_path': filepath, 'rel_path': rel_path, 'filename_no_ext': filename_no_ext, 'folder': folder}
                    else:
                        duplicate_files.append((key, existing['rel_path'], rel_path))
                    continue
                local_files[key] = {
                    'local_path': filepath,
                    'rel_path': rel_path,
                    'filename_no_ext': filename_no_ext,
                    'folder': folder
                }

    print(f"  [OK] Scanned {len(local_files)} tracks from G: Drive.")
    if duplicate_files:
        print(f"  [WARNING] {len(duplicate_files)} duplicate physical files found sharing an identity key with another file (not deleted, needs manual review):")
        for key, kept, discarded in duplicate_files:
            print(f"    - KEEP: {kept}  |  DUPLICATE: {discarded}")

    # 2. Load CSV Master Database
    print(f"\n[*] Loading master CSV database: {CSV_BLUEPRINT}...")
    if not os.path.exists(CSV_BLUEPRINT):
        print(f"[-] ERROR: CSV master database not found at: {CSV_BLUEPRINT}")
        sys.exit(1)

    rows = []
    fieldnames = []
    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames) if reader.fieldnames else []
        for row in reader:
            rows.append(row)

    # Ensure Pool and Explicit are in the fields list
    for field in ['Pool', 'Explicit']:
        if field not in fieldnames:
            fieldnames.append(field)

    print(f"  [OK] Loaded {len(rows)} database records.")

    # 3. Check for Broadcaster SQLite DB
    broadcaster_conn = None
    if BROADCASTER_DB.exists():
        print(f"\n[+] Detected Broadcaster SQLite Database: {BROADCASTER_DB}")
        if not DRY_RUN:
            broadcaster_conn = sqlite3.connect(BROADCASTER_DB, timeout=30.0)
            broadcaster_conn.execute("PRAGMA journal_mode=WAL;")
            broadcaster_conn.execute("PRAGMA busy_timeout=30000;")
            broadcaster_conn.row_factory = sqlite3.Row
    else:
        print("\n[-] Broadcaster SQLite Database not found locally. Skipping direct DB sync.")

    # 4. Resolve Discrepancies
    print("\n[*] Processing database rows for realignment...")
    realigned_count = 0
    missing_count = 0
    unchanged_count = 0

    local_keys_matched = set()

    for row in rows:
        track_name = row.get('Track Name', '').strip()
        csv_filepath_z = row.get('File Path', '').strip()
        
        # Get relative path from CSV file path (e.g. Z:/Era/Filename.mp3 -> Era/Filename.mp3)
        csv_rel_path = csv_filepath_z.replace('\\', '/')
        if csv_rel_path.upper().startswith('Z:/'):
            csv_rel_path = csv_rel_path[3:]
        elif csv_rel_path.lower().startswith('/home/ubuntu/music/'):
            csv_rel_path = csv_rel_path[len('/home/ubuntu/music/'):]

        # Path where the file should be locally on G: Drive
        expected_g_path = G_DRIVE_MUSIC / csv_rel_path

        if expected_g_path.exists():
            # Matches exactly!
            is_expl_row = 1 if str(row.get('Explicit')).lower() in ('true', '1', 'yes') else 0
            key = normalize_track_key(track_name, explicit_val=is_expl_row)
            local_keys_matched.add(key)
            # Ensure CSV has the pool assigned from folder if it doesn't have one
            if not row.get('Pool'):
                folder = csv_rel_path.split('/')[0] if '/' in csv_rel_path else ''
                pool_id = get_pool_id_from_folder(folder)
                if pool_id is not None:
                    row['Pool'] = str(pool_id)

            unchanged_count += 1
            
            # Check if it exists in Broadcaster DB!
            if not DRY_RUN and broadcaster_conn:
                cursor = broadcaster_conn.cursor()
                cursor.execute("SELECT id, music_pool_id FROM media_library WHERE file_path = ?", (csv_rel_path,))
                exists = cursor.fetchone()
                if not exists:
                    print(f"    [DB SYNC] Track '{track_name}' exists in CSV/disk but missing from DB. Inserting...")
                    try:
                        if " - " in track_name:
                            artist_part, title_part = track_name.split(" - ", 1)
                            artist = artist_part.strip()
                            title = title_part.strip()
                        else:
                            artist = "Unknown Artist"
                            title = track_name
                            
                        year_val = row.get('Year', 'Unknown')
                        try:
                            year_int = int(year_val)
                        except:
                            year_int = None
                            
                        explicit_val = 1 if str(row.get('Explicit')).lower() in ('true', '1') else 0
                        duration_ms = int(row.get('duration_ms') or 0)
                        folder = csv_rel_path.split('/')[0] if '/' in csv_rel_path else ''
                        category = get_era_category(folder)
                        
                        pool_val = row.get('Pool')
                        try:
                            pool_id = int(pool_val) if pool_val else None
                        except:
                            pool_id = None
                        if pool_id is None:
                            pool_id = get_pool_id_from_folder(folder)
                        
                        cursor.execute("""
                            INSERT INTO media_library (
                                title, artist, file_path, duration_ms, item_type, energy_category,
                                intro_duration, punch_ms, outro_duration, bpm, year, explicit, music_pool_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            title,
                            artist,
                            csv_rel_path,
                            duration_ms,
                            row.get('item_type', 'Music') or 'Music',
                            category,
                            int(row.get('Intro_Duration') or 0),
                            int(row.get('Punch_Ms') or 2000),
                            int(row.get('outro_duration') or 0),
                            int(row.get('bpm') or 98),
                            year_int,
                            explicit_val,
                            pool_id
                        ))
                        print(f"      [OK] Inserted track '{track_name}' into Broadcaster SQLite DB.")
                        db_updated = True
                    except Exception as db_err:
                        print(f"      [-] Failed to insert track in Broadcaster DB: {db_err}")
                else:
                    db_id, current_pool_id = exists
                    # Fetch current db metadata to compare and sync
                    try:
                        cursor.execute("SELECT intro_duration, outro_duration, punch_ms, bpm, music_pool_id FROM media_library WHERE id = ?", (db_id,))
                        db_row = cursor.fetchone()
                        if db_row:
                            db_intro, db_outro, db_punch, db_bpm, db_pool = db_row
                            
                            # Parse CSV values
                            try:
                                csv_intro = int(float(str(row.get('Intro_Duration') or row.get('Intro') or 0).strip()))
                            except:
                                csv_intro = 0
                            try:
                                csv_outro = int(float(str(row.get('outro_duration') or row.get('Outro') or 0).strip()))
                            except:
                                csv_outro = 0
                            try:
                                csv_punch = int(float(str(row.get('Punch_Ms') or row.get('Punch') or 2000).strip()))
                            except:
                                csv_punch = 2000
                            try:
                                csv_bpm = int(float(str(row.get('bpm') or row.get('BPM') or 98).strip()))
                            except:
                                csv_bpm = 98
                                
                            pool_val = row.get('Pool')
                            try:
                                csv_pool = int(pool_val) if pool_val else None
                            except:
                                csv_pool = None
                            if csv_pool is None:
                                folder = csv_rel_path.split('/')[0] if '/' in csv_rel_path else ''
                                csv_pool = get_pool_id_from_folder(folder)
                                
                            # Check what differs
                            updates = {}
                            if csv_intro != 0 and csv_intro != db_intro:
                                updates['intro_duration'] = csv_intro
                            if csv_outro != 0 and csv_outro != db_outro:
                                updates['outro_duration'] = csv_outro
                            if csv_punch != 2000 and csv_punch != db_punch:
                                updates['punch_ms'] = csv_punch
                            if csv_bpm != 98 and csv_bpm != db_bpm:
                                updates['bpm'] = csv_bpm
                            if csv_pool is not None and csv_pool != db_pool:
                                updates['music_pool_id'] = csv_pool
                                
                            if updates:
                                set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
                                sql_params = list(updates.values()) + [db_id]
                                print(f"    [DB SYNC] Updating metadata for existing track '{track_name}' (ID: {db_id}): {updates}")
                                cursor.execute(f"UPDATE media_library SET {set_clause} WHERE id = ?", sql_params)
                                db_updated = True
                    except Exception as db_sync_err:
                        print(f"      [-] Failed to sync metadata for track {track_name}: {db_sync_err}")
            continue

        # File does not exist at the CSV path! Search G: Drive by normalized key
        is_expl_row = 1 if str(row.get('Explicit')).lower() in ('true', '1', 'yes') else 0
        key = normalize_track_key(track_name, explicit_val=is_expl_row)
        if key in local_files:
            match = local_files[key]
            new_rel_path = match['rel_path']
            new_filename = match['filename_no_ext']
            new_folder = match['folder']
            new_local_path = match['local_path']

            local_keys_matched.add(key)
            realigned_count += 1

            old_z_path = csv_filepath_z
            new_z_path = new_rel_path

            print(f"\n  [REALIGNMENT DETECTED] '{track_name}'")
            print(f"    - Old: {old_z_path}")
            print(f"    - New: {new_z_path}")

            if not DRY_RUN and not SKIP_FTP:
                # A. Rename/move the file on Citrus3 FTP remote (Only if NOT explicit)
                is_explicit_val = str(row.get('Explicit', '')).lower() in ('1', 'true') or 'explicit' in new_rel_path.lower()
                if is_explicit_val:
                    print("    [FTP] Skipping remote move/copy since track is explicit.")
                else:
                    # If Z: drive mount is active, local file operations (which already happened or represent the same disk)
                    # make remote transfers redundant.
                    if str(G_DRIVE_MUSIC).upper().startswith("Z") or "Z:\\" in str(G_DRIVE_MUSIC):
                        print("    [FTP] Skipping remote move/copy since Z: drive mount is active (local matches remote).")
                    else:
                        print(f"    [FTP] Moving remote file from /{csv_rel_path} to /{new_rel_path}...")
                        cmd = [RCLONE_EXE, "moveto", f"citrus3:/{csv_rel_path}", f"citrus3:/{new_rel_path}", "--retries", "1", "--timeout", "10s", "--contimeout", "5s"]
                        res = subprocess.run(cmd, capture_output=True, timeout=15)
                        if res.returncode != 0:
                            print("      > FTP move failed (file may not exist on remote). Copying local file to remote...")
                            upload_cmd = [RCLONE_EXE, "copyto", str(new_local_path), f"citrus3:/{new_rel_path}", "--retries", "1", "--timeout", "10s", "--contimeout", "5s"]
                            try:
                                subprocess.run(upload_cmd, check=True, timeout=15)
                                print("      [OK] Uploaded to remote.")
                            except Exception as upload_err:
                                print(f"      [WARNING] Upload to Citrus3 FTP failed: {upload_err}. Proceeding with DB/CSV sync.")
                        else:
                            print("      [OK] Realigned on remote FTP.")

                # B. Update Broadcaster SQLite Database directly
                if broadcaster_conn:
                    cursor = broadcaster_conn.cursor()
                    try:
                        # Split new track name to artist & title
                        if " - " in new_filename:
                            artist_part, title_part = new_filename.split(" - ", 1)
                            artist = artist_part.strip()
                            title = title_part.strip()
                        else:
                            artist = "Unknown Artist"
                            title = new_filename

                        new_cat = get_era_category(new_folder)
                        new_pool_id = get_pool_id_from_folder(new_folder)
                        
                        # Update the file path, title, artist, energy_category, music_pool_id in media_library
                        cursor.execute('''
                            UPDATE media_library
                            SET file_path = ?, title = ?, artist = ?, energy_category = ?, music_pool_id = ?
                            WHERE file_path = ?
                        ''', (new_z_path, title, artist, new_cat, new_pool_id, old_z_path))
                        
                        if cursor.rowcount > 0:
                            print(f"      [OK] Updated Broadcaster SQLite DB row (preserved custom cues/history).")
                            db_updated = True
                        else:
                            print(f"      [-] No matching row found in Broadcaster DB for: {old_z_path}")
                    except Exception as ex:
                        print(f"      [-] Failed to update Broadcaster DB: {ex}")

                # C. Update CSV Row memory representation
                row['File Path'] = new_z_path
                row['Track Name'] = new_filename
                row['energy_category'] = get_era_category(new_folder)
                if new_pool_id is not None:
                    row['Pool'] = str(new_pool_id)
        else:
            # If G_DRIVE_MUSIC is the Z: rclone mount or we are on Linux (VM with rclone sync), local and remote are identical.
            # Attempting to copy from citrus3 is guaranteed to fail and wastes time.
            import platform
            if str(G_DRIVE_MUSIC).upper().startswith("Z") or "Z:\\" in str(G_DRIVE_MUSIC) or platform.system() != "Windows":
                print(f"  [MISSING FILE] '{track_name}' is missing from local library. Skipping download since Citrus3 FTP is mounted locally.")
                missing_count += 1
            else:
                print(f"  [MISSING FILE] '{track_name}' is missing from G: Drive. Skipping remote download fallback.")
                missing_count += 1

            # Even if the file is missing from disk, make sure the database has the correct pool ID from the CSV/energy_category
            if not DRY_RUN and broadcaster_conn:
                cursor = broadcaster_conn.cursor()
                cursor.execute("SELECT id, music_pool_id FROM media_library WHERE file_path = ?", (csv_rel_path,))
                exists = cursor.fetchone()
                if exists:
                    db_id, current_pool_id = exists
                    pool_val = row.get('Pool')
                    try:
                        pool_id = int(pool_val) if pool_val else None
                    except:
                        pool_id = None
                    if pool_id is None:
                        folder = csv_rel_path.split('/')[0] if '/' in csv_rel_path else ''
                        pool_id = get_pool_id_from_folder(folder)
                        if pool_id is None:
                            pool_id = get_pool_id_from_folder(row.get('energy_category', ''))
                        if pool_id is not None:
                            row['Pool'] = str(pool_id)
                    
                    if pool_id is not None and current_pool_id != pool_id:
                        print(f"    [DB SYNC] Updating pool ID {pool_id} for missing track '{track_name}' (ID: {db_id}) in DB")
                        cursor.execute("UPDATE media_library SET music_pool_id = ? WHERE id = ?", (pool_id, db_id))
                        db_updated = True


    # 5. Process New Untracked Files from G: Drive
    # untracked_keys was previously declared empty and never populated, making
    # this entire "import new tracks" feature permanently dead code - nothing
    # has ever been auto-imported by this script, regardless of what's sitting
    # unmatched on G: Drive.
    untracked_keys = sorted(set(local_files.keys()) - local_keys_matched)

    # is_non_song() was already imported and correctly identifies production/
    # sweeper/quarantine assets by path, but was previously only consulted for
    # one cosmetic field value (Explicit) deep in row construction, never as a
    # gate on whether to import at all. A misplaced production asset sitting
    # under Music/ (or a nested Music/ondemand/, Music/intro/, etc.) would get
    # imported as if it were a real song. Filter those out up front.
    non_song_keys = [k for k in untracked_keys if is_non_song(local_files[k]['filename_no_ext'], local_files[k]['rel_path'])]
    if non_song_keys:
        print(f"  [SKIP] {len(non_song_keys)} untracked file(s) identified as production/non-song assets by path, excluding from import:")
        for k in non_song_keys:
            print(f"    - {local_files[k]['rel_path']}")
    untracked_keys = [k for k in untracked_keys if k not in non_song_keys]

    # Windows/Google-Drive sync-collision duplicates: a filename ending in a
    # bare " (N)" (e.g. "Song (1).mp3") sitting alongside content that's
    # already cataloged under the non-suffixed name. These are not new
    # tracks - they're leftover duplicate copies from a sync collision.
    # Exclude any whose base name (suffix stripped) already exists in the
    # CSV. (A collision between two *untracked* candidates is already
    # caught above as a duplicate_files entry via the key-collision path.)
    current_csv_names = {(r.get('Track Name') or '').strip().lower() for r in rows}
    sync_dup_keys = []
    for k in untracked_keys:
        fn = local_files[k]['filename_no_ext']
        m = re.search(r'\s*\(\d+\)$', fn)
        if m and re.sub(r'\s*\(\d+\)$', '', fn).strip().lower() in current_csv_names:
            sync_dup_keys.append(k)
    if sync_dup_keys:
        print(f"  [SKIP] {len(sync_dup_keys)} untracked file(s) are sync-collision '(N)' duplicates of a track already in the CSV, excluding from import:")
        for k in sync_dup_keys:
            print(f"    - {local_files[k]['rel_path']}")
    untracked_keys = [k for k in untracked_keys if k not in sync_dup_keys]

    new_imported_rows = []

    if untracked_keys:
        print(f"\n[*] Found {len(untracked_keys)} new untracked tracks on G: Drive. Importing...")
        am = AutoMaster()
        
        for key in untracked_keys:
            match = local_files[key]
            filepath = match['local_path']
            rel_path = match['rel_path']
            new_filename = match['filename_no_ext']
            folder = match['folder']
            category = get_era_category(folder)
            pool_id = get_pool_id_from_folder(folder)
            
            print(f"  > Processing: {new_filename}...")
            
            if not DRY_RUN:
                # A. Extract tags & cues using AutoMaster FIRST
                print("    [*] Analyzing tags and cue points...")
                try:
                    audio = MP3(filepath)
                    length_sec = audio.info.length
                    length_str = f"{int(length_sec // 60)}:{int(length_sec % 60):02d}"
                    duration_ms = int(round(length_sec * 1000))
                    
                    file_path_str, meta_updates = am.process_file(str(filepath), original_bitrate="320k")

                    # Year isn't known until after AutoMaster's real tag/analysis
                    # pass, so this has to be a post-hoc skip rather than a
                    # pre-filter - per explicit review, tracks older than 1970
                    # are not auto-imported.
                    try:
                        release_year_val = int(str(meta_updates.get('release_year', '')).strip()[:4])
                    except (ValueError, TypeError):
                        release_year_val = None
                    if release_year_val is not None and release_year_val < 1970:
                        print(f"    [SKIP] '{new_filename}' is from {release_year_val} (pre-1970), excluding from import.")
                        continue

                    new_row = {}
                    for field in fieldnames:
                        lower_field = field.lower()
                        if field == 'Track Name':
                            new_row[field] = new_filename
                        elif field == 'File Path':
                            new_row[field] = rel_path
                        elif lower_field in ['source_url', 'url', 'source url']:
                            new_row[field] = ""
                        elif field == 'duration_ms':
                            new_row[field] = duration_ms
                        elif field == 'item_type':
                            new_row[field] = 'Music'
                        elif field in ['energy_category', 'Energy Category']:
                            new_row[field] = category
                        elif field == 'Pool':
                            new_row[field] = str(pool_id) if pool_id is not None else ""
                        elif field == 'Intro_Duration':
                            new_row[field] = meta_updates.get('intro_duration', 0)
                        elif field == 'Punch_Ms':
                            new_row[field] = meta_updates.get('punch_ms', 2000)
                        elif field == 'outro_duration':
                            new_row[field] = meta_updates.get('outro_duration', 0)
                        elif field == 'bpm':
                            new_row[field] = meta_updates.get('bpm', 98)
                        elif field == 'Bitrate':
                            new_row[field] = '320k'
                        elif field == 'Lyrics':
                            new_row[field] = 'True' if meta_updates.get('lyrics') and meta_updates.get('lyrics') != 'Not Found' else 'Unknown'
                        elif lower_field in ['year', 'true_year', 'release_year']:
                            new_row[field] = meta_updates.get('release_year', 'Unknown')
                        elif lower_field in ['art ratio', 'art_ratio']:
                            new_row[field] = '1.0'
                        elif field == 'Length':
                            new_row[field] = length_str
                        elif field == 'Explicit':
                            title_lower = new_filename.lower()
                            if is_non_song(new_filename, str(rel_path)):
                                new_row[field] = 'False'
                            elif 'explicit' in title_lower:
                                new_row[field] = 'True'
                            elif 'clean' in title_lower:
                                new_row[field] = 'False'
                            else:
                                new_row[field] = 'Unknown'
                        else:
                            new_row[field] = ""
                    
                    new_imported_rows.append(new_row)
                    print(f"    [✓] Generated metadata for: {new_filename}")
                    
                    # B. Copy to Citrus3 FTP server (ONLY if not explicit)
                    if not SKIP_FTP:
                        is_expl_val = new_row.get('Explicit') == 'True' or 'explicit' in new_filename.lower() or 'explicit' in rel_path.lower()
                        if is_expl_val:
                            print("    [FTP] Skipping remote upload since track is explicit.")
                        else:
                            # If Z: drive mount is active, local file operations
                            # make remote transfers redundant.
                            if str(G_DRIVE_MUSIC).upper().startswith("Z") or "Z:\\" in str(G_DRIVE_MUSIC):
                                print("    [FTP] Skipping remote upload since Z: drive mount is active (local matches remote).")
                            else:
                                print(f"    [FTP] Uploading to citrus3:/{rel_path}...")
                                upload_cmd = [RCLONE_EXE, "copyto", str(filepath), f"citrus3:/{rel_path}", "--retries", "1", "--timeout", "10s"]
                                try:
                                    subprocess.run(upload_cmd, check=True)
                                    print("    [OK] Upload completed.")
                                except Exception as upload_err:
                                    print(f"    [WARNING] Upload to Citrus3 FTP failed: {upload_err}. Proceeding with local DB import.")
                    else:
                        print("    [FTP] Skipping FTP upload (SKIP_FTP enabled).")
                    
                    if broadcaster_conn:
                        try:
                            cursor = broadcaster_conn.cursor()
                            if " - " in new_filename:
                                artist_part, title_part = new_filename.split(" - ", 1)
                                artist = artist_part.strip()
                                title = title_part.strip()
                            else:
                                artist = "Unknown Artist"
                                title = new_filename
                                
                            year_val = meta_updates.get('release_year', 'Unknown')
                            try:
                                year_int = int(year_val)
                            except:
                                year_int = None
                                
                            explicit_val = 1 if new_row.get('Explicit') == 'True' else 0

                            # This file was "untracked" only relative to the CSV -
                            # the DB (local or VM) may already have a real row for
                            # this exact file_path from a prior sync that never made
                            # it into the CSV. Check first rather than blindly
                            # INSERTing and hitting the UNIQUE(file_path) constraint.
                            cursor.execute("SELECT id FROM media_library WHERE file_path = ?", (rel_path,))
                            existing = cursor.fetchone()
                            if existing:
                                print(f"      [OK] '{new_filename}' already exists in Broadcaster SQLite DB (id {existing[0]}) - CSV was the only thing out of date, nothing to insert.")
                            else:
                                cursor.execute("""
                                    INSERT INTO media_library (
                                        title, artist, file_path, duration_ms, item_type, energy_category,
                                        intro_duration, punch_ms, outro_duration, bpm, year, explicit, music_pool_id
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (
                                    title,
                                    artist,
                                    rel_path,
                                    duration_ms,
                                    'Music',
                                    category,
                                    meta_updates.get('intro_duration', 0),
                                    meta_updates.get('punch_ms', 2000),
                                    meta_updates.get('outro_duration', 0),
                                    meta_updates.get('bpm', 98),
                                    year_int,
                                    explicit_val,
                                    pool_id
                                ))
                                print(f"      [OK] Inserted new track '{new_filename}' into Broadcaster SQLite DB.")
                                db_updated = True
                        except Exception as db_err:
                            print(f"      [-] Failed to insert new track in Broadcaster DB: {db_err}")
                except Exception as e:
                    print(f"    [-] Failed to process metadata: {e}")

    # 6. Save DB and Commit SQLite transactions
    if not DRY_RUN:
        if realigned_count > 0 or len(new_imported_rows) > 0 or db_updated:
            # Backup CSV first
            csv_path = Path(CSV_BLUEPRINT)
            backup_csv = csv_path.with_name(csv_path.name + ".sync_backup")
            shutil.copy2(CSV_BLUEPRINT, backup_csv)
            print(f"\n[*] Backed up master database to {backup_csv.name}")

            # Save CSV
            all_rows = rows + new_imported_rows
            with open(CSV_BLUEPRINT, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(all_rows)
            print(f"[✓] Saved updated CSV master database with {len(all_rows)} records.")

            # Commit SQLite database changes
            if broadcaster_conn:
                broadcaster_conn.commit()
                broadcaster_conn.close()
                print("[✓] Committed Broadcaster SQLite database changes.")

            # 7. Git Commit & Push
            if AUTO_GIT_PUSH:
                try:
                    # This script runs unattended every ~3 minutes via cron,
                    # concurrently with the long-running app.py service's own
                    # git-sync sites (git_sync_worker, _git_auto_push, etc.).
                    # This previously had no locking against any of them - two
                    # `git pull --rebase` calls landing at the same moment
                    # produced a real, stuck merge conflict with literal
                    # <<<<<<< markers baked into the live CSV for ~50 minutes
                    # before anything noticed (2026-09-03). Sharing the same
                    # cross-process lock every other git-sync site now uses.
                    with git_operation_lock(timeout=60):
                        print("\n[*] Committing database updates to GitHub...")
                        script_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                        subprocess.run(["git", "add", "configs/fmp_data_7718.csv"], check=True, capture_output=True, cwd=script_root)
                        msg = f"Auto-Sync: Realigned {realigned_count} tracks, imported {len(new_imported_rows)} new tracks"
                        # Pathspec-restricted commit so this cannot sweep up whatever
                        # else happens to be staged (same bug/fix as storage.py's own
                        # _git_auto_push).
                        subprocess.run(["git", "commit", "configs/fmp_data_7718.csv", "-m", msg, "--no-verify"], check=True, capture_output=True, cwd=script_root)
                        res_branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, check=True, cwd=script_root)
                        branch_name = res_branch.stdout.strip()
                        # Merge, not rebase+autostash - see config.git_safe_pull's
                        # docstring for the 2026-09-03 incident this replaces.
                        git_safe_pull(branch_name, cwd=script_root)
                        subprocess.run(["git", "push", "origin", branch_name], check=True, capture_output=True, cwd=script_root)
                        print("  [OK] Pushed successfully to GitHub.")
                except Exception as e:
                    print(f"  [-] Git push failed: {e}")

            # 8. Trigger MediaCP reindex
            print("\n[*] Triggering MediaCP play server reindex...")
            try:
                # Call MediaCP reindex endpoint
                url = "https://hello.citrus3.com:2020/controller/Media/1073/reindex"
                req = urllib.request.Request(url, headers={'User-Agent': 'FMP-Sync-Engine'})
                urllib.request.urlopen(req, timeout=5)
                print("  [OK] MediaCP reindex triggered.")
            except Exception as e:
                print(f"  [-] MediaCP reindex trigger failed (play server may require manual reindex): {e}")

            # 9. Trigger Broadcaster Sync (FastAPI / Route)
            print("\n[*] Triggering Broadcaster local server sync...")
            try:
                url = "http://127.0.0.1:8000/"
                req = urllib.request.Request(url, headers={'User-Agent': 'FMP-Sync-Engine'})
                urllib.request.urlopen(req, timeout=2)
                print("  [OK] Broadcaster notified. Local database sync scheduled.")
            except Exception as e:
                print("  [-] Broadcaster server is currently offline or unreachable. (Will sync upon next boot).")
        else:
            print("\n[OK] No database updates or realignments were needed. Everything is already in sync.")
            if broadcaster_conn:
                broadcaster_conn.close()

    # 10. Print beautiful Summary
    print("\n" + "="*80)
    print(" AUTOMATED SYNC ENGINE SUMMARY REPORT")
    print("-"*80)
    print(f" Unchanged Tracks (Perfect Alignment): {unchanged_count}")
    print(f" Realigned Tracks (Moved/Renamed):     {realigned_count}")
    print(f" New Untracked Tracks Imported:        {len(new_imported_rows)}")
    print(f" Missing Tracks (Flagged/Unresolved):  {missing_count}")
    print("="*80 + "\n")

    # 11. Write to local persistent log file for explicit tracking
    log_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "sync_history.log"
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] Unchanged: {unchanged_count} | Realigned: {realigned_count} | Imported: {len(new_imported_rows)} | Missing: {missing_count}\n"
    try:
        with open(log_file, "a", encoding="utf-8") as lf:
            lf.write(log_line)
        print(f"  [OK] Sync results logged to {log_file.name}")
    except Exception as le:
        print(f"  [-] Failed to write to log file: {le}")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Sync the music library CSV/DB against physical files")
    parser.add_argument("--live", action="store_true", help="Actually write the CSV/DB and push to git (default is a dry run)")
    args = parser.parse_args()
    if args.live:
        DRY_RUN = False
    run_sync()
