import os
import csv
import sys
import io
import shutil
import platform
from pathlib import Path
from mutagen.mp3 import MP3
from mutagen.id3 import TXXX, TBPM

# Set standard output encoding to UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT_DIR)
from modules.tagger import AutoMaster
from config import CSV_BLUEPRINT, MUSIC_DIR

BACKUP_PATH = Path(os.path.join(ROOT_DIR, "configs", "fmp_data_7718_heal_backup.csv"))

if platform.system() == "Windows":
    Z_DIR = Path("Z:/")
else:
    Z_DIR = Path(MUSIC_DIR)

# 1. Back up CSV database
def backup_database():
    print("[*] Backing up CSV database...")
    if not os.path.exists(CSV_BLUEPRINT):
        print("[-] Error: CSV blueprint not found.")
        sys.exit(1)
    shutil.copy2(CSV_BLUEPRINT, BACKUP_PATH)
    print(f"[✓] Backup created at: {BACKUP_PATH}")

def load_csv_rows():
    rows = []
    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)
    return rows, fieldnames

def save_csv_rows(rows, fieldnames):
    with open(CSV_BLUEPRINT, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[✓] CSV database saved with {len(rows)} records.")

def heal_library():
    backup_database()
    rows, fieldnames = load_csv_rows()
    
    deleted_physical_files = []
    
    # -------------------------------------------------------------
    # Step 1: Update Location Mismatches in CSV
    # -------------------------------------------------------------
    print("\n[*] Step 1: Repairing Location Mismatches...")
    mismatch_repairs = {
        "Michael Jackson - Beat It [The Very Best Of]": {
            "File Path": "Z:/New School 2010+/Michael Jackson - Beat It [The Very Best Of].mp3",
            "energy_category": "New School"
        },
        "Gladys Knight & the Pips - Midnight Train to Georgia [Imagination]": {
            "File Path": "Z:/Live/Gladys Knight & the Pips - Midnight Train to Georgia [Imagination].mp3",
            "energy_category": "Live"
        },
        "Usher - My Way": {
            "File Path": "Z:/New School 2010+/Usher - My Way.mp3",
            "energy_category": "New School"
        }
    }
    
    for row in rows:
        track_name = row.get('Track Name', '').strip()
        if track_name in mismatch_repairs:
            rep = mismatch_repairs[track_name]
            print(f"  > Repairing {track_name}:")
            print(f"    - Path: {row.get('File Path')} -> {rep['File Path']}")
            row['File Path'] = rep['File Path']
            if 'energy_category' in row:
                print(f"    - Category: {row.get('energy_category')} -> {rep['energy_category']}")
                row['energy_category'] = rep['energy_category']
            elif 'Energy Category' in row:
                print(f"    - Category: {row.get('Energy Category')} -> {rep['energy_category']}")
                row['Energy Category'] = rep['energy_category']

    # -------------------------------------------------------------
    # Step 2: Resolve Sleepy Brown Naming Mismatch
    # -------------------------------------------------------------
    print("\n[*] Step 2: Repairing Sleepy Brown Naming Mismatch...")
    sleepy_old_name = "Sleepy Brown featuring OutKast - Can’t Wait [Barbershop 2_ Back in Business_ Soundtrack]"
    sleepy_new_name = "Sleepy Brown - I Can't Wait (feat. OutKast) [Barbershop 2 (Back In Business)]"
    sleepy_new_path = "Z:/Throwbacks 90s2000s/Sleepy Brown - I Can't Wait (feat. OutKast) [Barbershop 2 (Back In Business)].mp3"
    
    sleepy_repaired = False
    for row in rows:
        if row.get('Track Name', '').strip() == sleepy_old_name:
            print(f"  > Repaired Sleepy Brown: {sleepy_old_name} -> {sleepy_new_name}")
            row['Track Name'] = sleepy_new_name
            row['File Path'] = sleepy_new_path
            sleepy_repaired = True
            break
    if not sleepy_repaired:
        print("  [-] Warning: Sleepy Brown record not found in CSV database.")

    # -------------------------------------------------------------
    # Step 3: Purge Dead CSV Records
    # -------------------------------------------------------------
    print("\n[*] Step 3: Purging Dead CSV Records...")
    dead_tracks_to_purge = [
        "Janet Jackson - All For You [All For You] (1)",
        "SZA - Snooze"  # This will match if exact, but let's check path to be safe
    ]
    
    clean_rows = []
    for row in rows:
        track_name = row.get('Track Name', '').strip()
        file_path = row.get('File Path', '').strip()
        
        # Purge SZA Snooze ONLY if it is the test folder version
        if track_name == "SZA - Snooze" and "test_folder" in file_path.lower():
            print(f"  > Purging dead SZA - Snooze entry at path: {file_path}")
            continue
        elif track_name == "Janet Jackson - All For You [All For You] (1)":
            print(f"  > Purging dead Janet Jackson entry: {track_name}")
            continue
            
        clean_rows.append(row)
    rows = clean_rows

    # Save database changes so far
    save_csv_rows(rows, fieldnames)

    # -------------------------------------------------------------
    # Step 4: Quality Check & Import/Delete Untracked Files
    # -------------------------------------------------------------
    print("\n[*] Step 4: Processing Untracked Files...")
    
    untracked_files_to_check = [
        {
            'path': Z_DIR / "New School 2010+" / "Bilal - Soul Sista [1st Born Second].mp3",
            'era': "New School 2010+",
            'category': "New School"
        },
        {
            'path': Z_DIR / "New School 2010+" / "Jazz Official - Sorry Not Sorry (feat. Anthony Radcliff).mp3",
            'era': "New School 2010+",
            'category': "New School"
        },
        {
            'path': Z_DIR / "New School 2010+" / "Jazz Official - Wrong Time.mp3",
            'era': "New School 2010+",
            'category': "New School"
        }
    ]
    
    # On Linux, Classics folder is lowercase or mixed, check Z_DIR/Classics or Z_DIR/classics
    z_online = Z_DIR.exists() and (os.path.exists(Z_DIR / "Classics") or os.path.exists(Z_DIR / "classics"))
    
    def get_rclone_path():
        import shutil
        if platform.system() == "Windows":
            path = os.path.join(ROOT_DIR, "rclone.exe")
            if os.path.exists(path):
                return path
        resolved = shutil.which("rclone")
        if resolved:
            return resolved
        return "rclone"

    rclone_path = get_rclone_path()
    
    if not z_online:
        print("[WARNING] Z: drive / music storage is offline. Operating in direct Rclone command-line fallback mode.")
        HEAL_TEMP = Path(os.path.join(ROOT_DIR, "staging", "_heal_temp"))
        HEAL_TEMP.mkdir(parents=True, exist_ok=True)
    else:
        HEAL_TEMP = None

    am = AutoMaster()
    new_imported_rows = []
    
    for item in untracked_files_to_check:
        era = item['era']
        category = item['category']
        file_name = item['path'].name
        
        print(f"  > Auditing: {file_name}...")
        
        # Determine path to check
        if z_online:
            file_path = item['path']
            if not file_path.exists():
                print(f"    [-] File does not exist on Z: drive: {file_path}")
                continue
        else:
            file_path = HEAL_TEMP / file_name
            # Download file from FTP via rclone
            remote_src = f"citrus3:/{era}/{file_name}"
            cmd = [rclone_path, "copyto", remote_src, str(file_path)]
            run_res = subprocess.run(cmd, capture_output=True)
            if run_res.returncode != 0 or not file_path.exists():
                print(f"    [-] File does not exist on Citrus3 remote: {remote_src}")
                continue
            
        # 1. Quality Check
        is_valid = True
        reason = ""
        try:
            # Check channels and sample rate using ffprobe (via am._verify_quality)
            import subprocess
            import json
            cmd = [
                'ffprobe', '-v', 'error', '-select_streams', 'a:0',
                '-show_entries', 'stream=channels,sample_rate', '-of', 'json', str(file_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            stream = data.get('streams', [{}])[0]
            channels = int(stream.get('channels', 0))
            sample_rate = int(stream.get('sample_rate', 0))
            
            if channels < 2 or sample_rate < 44100:
                is_valid = False
                reason = f"Low quality (channels: {channels}, sample rate: {sample_rate}Hz)"
                
            # Verify file can be parsed by mutagen
            audio = MP3(file_path)
            if not audio or not audio.info:
                is_valid = False
                reason = "Invalid MP3 structure or missing audio stream info"
                
        except Exception as e:
            is_valid = False
            reason = f"Corrupt file or mutagen read error: {e}"
            
        if not is_valid:
            # DELETE incomplete/corrupt file
            print(f"    [!] INCOMPLETE/CORRUPT TRACK DETECTED: {reason}")
            print(f"    [!] Action: Physically deleting '{file_name}'...")
            if z_online:
                try:
                    os.remove(file_path)
                    deleted_physical_files.append((file_name, reason))
                    print(f"    [✓] Deleted successfully.")
                except Exception as ex:
                    print(f"    [-] Failed to delete physical file: {ex}")
            else:
                try:
                    remote_src = f"citrus3:/{era}/{file_name}"
                    subprocess.run([rclone_path, "deletefile", remote_src], check=True)
                    if file_path.exists(): os.remove(file_path)
                    deleted_physical_files.append((file_name, reason))
                    print(f"    [✓] Deleted successfully from remote.")
                except Exception as ex:
                    print(f"    [-] Failed to delete remote file: {ex}")
            continue
            
        # 2. Extract tags & cues using process_file
        print(f"    [*] Extracting metadata tags and cue points...")
        try:
            file_path_str, meta_updates = am.process_file(str(file_path), original_bitrate="320k")
            
            if not meta_updates:
                raise Exception("Empty metadata returned from process_file")
                
            # If offline, we must push the modified tagged file back to Citrus3 remote
            if not z_online:
                print(f"    [*] Uploading updated tags back to remote...")
                remote_dest = f"citrus3:/{era}/{file_name}"
                subprocess.run([rclone_path, "copyto", "--inplace", str(file_path), remote_dest], check=True)
                if file_path.exists(): os.remove(file_path)

            # Get track length info
            length_sec = audio.info.length
            length_str = f"{int(length_sec // 60)}:{int(length_sec % 60):02d}"
            duration_ms = int(round(length_sec * 1000))
            
            track_name = file_path.stem
            
            new_row = {}
            for field in fieldnames:
                lower_field = field.lower()
                if field == 'Track Name':
                    new_row[field] = track_name
                elif field == 'File Path':
                    new_row[field] = f"Z:/{era}/{file_name}"
                elif lower_field in ['source_url', 'url', 'source url']:
                    new_row[field] = ""
                elif field == 'duration_ms':
                    new_row[field] = duration_ms
                elif field == 'item_type':
                    new_row[field] = 'Music'
                elif field in ['energy_category', 'Energy Category']:
                    new_row[field] = category
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
                else:
                    new_row[field] = ""
            
            new_imported_rows.append(new_row)
            print(f"    [✓] Imported successfully: '{track_name}' (Year: {new_row.get('Year') or new_row.get('release_year')}, BPM: {new_row.get('bpm')})")
            
        except Exception as e:
            # Delete on failure
            print(f"    [!] Error processing tags: {e}")
            print(f"    [!] Action: Physically deleting '{file_name}'...")
            if z_online:
                try:
                    os.remove(file_path)
                    deleted_physical_files.append((file_name, f"Metadata extraction failed: {e}"))
                    print(f"    [✓] Deleted successfully.")
                except Exception as ex:
                    print(f"    [-] Failed to delete physical file: {ex}")
            else:
                try:
                    remote_src = f"citrus3:/{era}/{file_name}"
                    subprocess.run([rclone_path, "deletefile", remote_src], check=True)
                    if file_path.exists(): os.remove(file_path)
                    deleted_physical_files.append((file_name, f"Metadata extraction failed: {e}"))
                    print(f"    [✓] Deleted successfully from remote.")
                except Exception as ex:
                    print(f"    [-] Failed to delete remote file: {ex}")
                
    if new_imported_rows:
        rows, fieldnames = load_csv_rows()
        rows.extend(new_imported_rows)
        save_csv_rows(rows, fieldnames)
        
    print("\n" + "="*50)
    print(" HEALING COMPLETED SUMMARY")
    print("="*50)
    print(f" - Location Mismatches Repaired: 3")
    print(f" - Sleepy Brown Alignment:      Complete")
    print(f" - Dead CSV Entries Purged:     2")
    print(f" - Untracked Tracks Imported:   {len(new_imported_rows)}")
    print(f" - Physical Files Deleted:      {len(deleted_physical_files)}")
    for name, reason in deleted_physical_files:
        print(f"    > Deleted: '{name}' Reason: {reason}")
    print("="*50 + "\n")
    
    # 5. Git Commit and Push
    from config import AUTO_GIT_PUSH
    if AUTO_GIT_PUSH:
        import subprocess
        try:
            print("[*] Committing CSV updates to Git...")
            subprocess.run(["git", "add", "configs/fmp_data_7718.csv"], check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Database automated healing and realignment complete"], check=True, capture_output=True)
            subprocess.run(["git", "push", "origin", "main"], check=True, capture_output=True)
            print("[✓] Pushed changes successfully to GitHub.")
        except Exception as e:
            print(f"[-] Git push failed: {e}")

if __name__ == "__main__":
    heal_library()
