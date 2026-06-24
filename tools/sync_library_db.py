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
from config import CSV_BLUEPRINT, AUTO_GIT_PUSH, is_non_song

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
DRY_RUN = False  # Change to True to print actions without committing them

def normalize_track_key(name: str) -> str:
    if not name:
        return ""
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
    title_clean = re.sub(r'[^a-z0-9]', '', title_clean)
    
    return f"{artist_clean}_{title_clean}"

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
            subprocess.run(["git", "pull", "--rebase"], check=True, capture_output=True)
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
                if key:
                    local_files[key] = {
                        'local_path': filepath,
                        'rel_path': rel_path,
                        'filename_no_ext': filename_no_ext,
                        'folder': folder
                    }

    print(f"  [OK] Scanned {len(local_files)} tracks from G: Drive.")

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
            key = normalize_track_key(track_name)
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
        key = normalize_track_key(track_name)
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

            if not DRY_RUN:
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
                        cmd = [RCLONE_EXE, "moveto", f"citrus3:/{csv_rel_path}", f"citrus3:/{new_rel_path}"]
                        res = subprocess.run(cmd, capture_output=True)
                        if res.returncode != 0:
                            print("      > FTP move failed (file may not exist on remote). Copying local file to remote...")
                            upload_cmd = [RCLONE_EXE, "copyto", str(new_local_path), f"citrus3:/{new_rel_path}"]
                            try:
                                subprocess.run(upload_cmd, check=True)
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
    untracked_keys = set(local_files.keys()) - local_keys_matched
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
                    print("\n[*] Committing database updates to GitHub...")
                    subprocess.run(["git", "add", "configs/fmp_data_7718.csv"], check=True, capture_output=True)
                    msg = f"Auto-Sync: Realigned {realigned_count} tracks, imported {len(new_imported_rows)} new tracks"
                    subprocess.run(["git", "commit", "-m", msg, "--no-verify"], check=True, capture_output=True)
                    subprocess.run(["git", "push", "origin", "HEAD"], check=True, capture_output=True)
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
    run_sync()
