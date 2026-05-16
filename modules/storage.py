import os
import shutil
import csv
import logging
import threading
import time
from ftplib import FTP, error_perm
from typing import List, Tuple, Dict
from thefuzz import process 
from mutagen.mp3 import MP3

from config import CSV_BLUEPRINT, STAGING_DIR, FTP_HOST, FTP_USER, FTP_PASS, FTP_BASE_DIR, FTP_PORT

class TransmissionError(Exception):
    """Custom exception for FTP transmission failures."""
    pass

class VaultManager:
    # Class-level lock to ensure all instances of VaultManager synchronize CSV access
    _csv_lock = threading.Lock()

    def __init__(self):
        self.era_folders = ["Classics", "Old School 70s80s", "Throwbacks 90s2000s", "New School 2010+", "Live", "Unsorted_Review", "intro", "ondemand", "365 Commercials"]

    def _connect_ftp(self):
        """Connects to FTP with 3 retry attempts and exponential backoff."""
        attempts = 3
        delay = 2
        for i in range(attempts):
            try:
                ftp = FTP()
                ftp.connect(FTP_HOST, FTP_PORT, timeout=15) 
                ftp.login(FTP_USER, FTP_PASS)
                ftp.set_pasv(True) 
                return ftp
            except Exception as e:
                if i == attempts - 1:
                    logging.error(f"FTP Connection failed after {attempts} attempts: {e}")
                    raise
                time.sleep(delay)
                delay *= 2

    def _ensure_remote_dir(self, ftp, directory: str):
        try:
            ftp.cwd(directory)
        except error_perm:
            ftp.cwd(FTP_BASE_DIR)
            ftp.mkd(directory)
            ftp.cwd(directory)

    def find_candidates(self, query: str) -> List[Dict]:
        with self._csv_lock:
            if not os.path.exists(CSV_BLUEPRINT): return []
            query_clean = query.lower().strip()
            if not query_clean: return []
            
            db = {}
            with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    db[row['Track Name']] = row
            
            results_dict = {}
            for track_name, row in db.items():
                if query_clean in track_name.lower():
                    data = row.copy()
                    data['name'] = track_name
                    data['score'] = 100
                    results_dict[track_name] = data
                    
            matches = process.extract(query, list(db.keys()), limit=None)
            for match_name, score in matches:
                if score >= 70 and match_name not in results_dict:
                    data = db[match_name].copy()
                    data['name'] = match_name
                    data['score'] = score
                    results_dict[match_name] = data

            sorted_results = list(results_dict.values())
            sorted_results.sort(key=lambda x: x['score'], reverse=True)
            return sorted_results

    def scrub_track(self, exact_title: str) -> Tuple[bool, str]:
        attempts = 3
        delay = 2
        server_deleted = False
        target_filename = f"{exact_title}.mp3".lower()
        
        for i in range(attempts):
            ftp = None
            try:
                ftp = self._connect_ftp()
                search_dirs = [f"/{folder}" for folder in self.era_folders]
                
                try:
                    ftp.cwd("/Shows")
                    for show in ftp.nlst():
                        if not show.endswith('.mp3'): search_dirs.append(f"/Shows/{show}")
                except: pass

                for remote_dir in search_dirs:
                    try:
                        ftp.cwd(remote_dir)
                        items = ftp.nlst()
                        for item in items:
                            if item.lower() == target_filename:
                                ftp.delete(item)
                                server_deleted = True
                                break
                        if server_deleted: break
                    except: continue
                
                ftp.quit()
                break # Success (either deleted or definitely not found)
            except Exception as e:
                if i == attempts - 1:
                    print(f"[ERASER ERROR] FTP failed after {attempts} attempts: {e}")
                    break
                time.sleep(delay)
                delay *= 2
            finally:
                if ftp: 
                    try: 
                        ftp.close() 
                    except: 
                        pass

        # 2. Unconditionally clean the local CSV so the UI is accurate
        found_in_csv = False
        with self._csv_lock:
            rows = []
            if os.path.exists(CSV_BLUEPRINT):
                with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    fieldnames = reader.fieldnames
                    for row in reader:
                        if row['Track Name'] == exact_title: found_in_csv = True
                        else: rows.append(row)
                
                with open(CSV_BLUEPRINT, 'w', encoding='utf-8', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)

        if server_deleted and found_in_csv:
            return True, "Erased from Server and Database."
        elif server_deleted and not found_in_csv:
            return True, "Erased from Server (was not in Database)."
        elif not server_deleted and found_in_csv:
            return True, "Removed from Database (Could not locate physical file on Server)."
        else:
            return False, "Track not found anywhere."

    def store_track(self, file_path: str, metadata: dict, task_id: str = "", target_override: str = None) -> Tuple[bool, str]:
        try:
            clean_name = os.path.basename(file_path)
            track_name = clean_name.replace('.mp3', '')
            release_year = metadata.get('release_year', 'Unknown')
            
            # [PHASE E] Duplicate Guard
            # Intercept before any network overhead if the track is already in the database
            with self._csv_lock:
                if os.path.exists(CSV_BLUEPRINT):
                    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            if row.get('Track Name') == track_name:
                                return False, "Duplicate Track Detected"

            if target_override: 
                remote_target = f"/{target_override}"
            else:
                # [PHASE F] Live Detection
                # Prioritize dedicated /Live folder if the filename suggests it's a performance
                if "live" in clean_name.lower():
                    era_folder = "Live"
                elif not release_year or release_year == 'Unknown': 
                    era_folder = "Unsorted_Review"
                else:
                    year_int = int(str(release_year)[:4])
                    if year_int < 1970: era_folder = "Classics"
                    elif 1970 <= year_int <= 1989: era_folder = "Old School 70s80s"
                    elif 1990 <= year_int <= 2009: era_folder = "Throwbacks 90s2000s"
                    else: era_folder = "New School 2010+"
                remote_target = f"/{era_folder}"

            attempts = 3
            delay = 2
            transmission_success = False
            last_error = "Unknown Error"

            for i in range(attempts):
                ftp = None
                try:
                    ftp = self._connect_ftp()
                    
                    if target_override and target_override.startswith("Shows/"):
                        try: ftp.cwd("/Shows")
                        except error_perm: ftp.mkd("/Shows")

                    self._ensure_remote_dir(ftp, remote_target)
                    ftp.cwd(remote_target)
                    
                    print(f"\n[VAULT] Attempt {i+1}/{attempts}: Transmitting [{clean_name}] to [{remote_target}]...")
                    with open(file_path, 'rb') as local_file:
                        ftp.storbinary(f"STOR {clean_name}", local_file)
                    
                    # Integrity Check: Compare remote vs local size
                    local_size = os.path.getsize(file_path)
                    try:
                        remote_size = ftp.size(clean_name)
                    except:
                        # Fallback to nlst if SIZE command is not supported
                        remote_size = local_size if clean_name in ftp.nlst() else -1
                    
                    if remote_size != local_size:
                        raise TransmissionError(f"Integrity check failed: Local {local_size} vs Remote {remote_size}")

                    ftp.quit()
                    transmission_success = True
                    print("[VAULT] Transfer verified.")
                    break
                except Exception as e:
                    last_error = str(e)
                    print(f"[VAULT ERROR] Attempt {i+1} failed: {e}")
                    if i == attempts - 1: break
                    time.sleep(delay)
                    delay *= 2
                finally:
                    if ftp: 
                        try: ftp.close() 
                        except: pass

            if not transmission_success:
                logging.error(f"FTP Vault storage failed after {attempts} attempts.")
                return False, f"Transmission Error: {last_error}"

            try:
                # Only update CSV if the transfer was verified
                audio = MP3(file_path)
                length_str = f"{int(audio.info.length // 60)}:{int(audio.info.length % 60):02d}" if audio else "0:00"

                # [SCHEMA EXPANSION & OVERRIDE PROTECTION]
                new_row = {
                    'Track Name': track_name,
                    'Bitrate': metadata.get('bitrate', '320k'),
                    'Lyrics': metadata.get('lyrics', 'Not Found'),
                    'True_Year': metadata.get('release_year', 'Verify Year'),
                    'Art Ratio': metadata.get('art_ratio', '1.0'),
                    'Length': length_str,
                    'Source_URL': metadata.get('url', ""),
                    'BPM': metadata.get('bpm', 0),
                    'Intro_Sec': metadata.get('intro_sec', 0),
                    'Cue_In': metadata.get('cue_in', 0.0),
                    'Cue_Out': metadata.get('cue_out', 0.0),
                    'Override': 'No' # Algorithmic by default
                }

                fieldnames = ['Track Name', 'Bitrate', 'Lyrics', 'True_Year', 'Art Ratio', 'Length', 'Source_URL', 'BPM', 'Intro_Sec', 'Cue_In', 'Cue_Out', 'Override']

                with self._csv_lock:
                    file_exists = os.path.exists(CSV_BLUEPRINT)
                    with open(CSV_BLUEPRINT, 'a', encoding='utf-8', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        if not file_exists: writer.writeheader()
                        writer.writerow(new_row)

                return True, "Success"
            except Exception as e:
                logging.error(f"Local database update failed: {e}")
                return False, f"Database Error: {e}"

    def update_track_metadata(self, track_name: str, updates: dict) -> Tuple[bool, str]:
        """
        [OVERRIDE PROTECTION]
        Pushes manual user corrections to the database and locks them.
        """
        with self._csv_lock:
            if not os.path.exists(CSV_BLUEPRINT): 
                return False, "Database not found."
            
            rows = []
            updated = False
            fieldnames = []
            
            try:
                with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    fieldnames = reader.fieldnames
                    # Ensure Override column exists in logic if the file is old
                    if 'Override' not in fieldnames:
                        fieldnames.append('Override')

                    for row in reader:
                        if row.get('Track Name') == track_name:
                            row.update(updates)
                            row['Override'] = "Yes" # LOCK THE ROW
                            updated = True
                        rows.append(row)
                
                if updated:
                    with open(CSV_BLUEPRINT, 'w', encoding='utf-8', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)
                    return True, f"Manual Override locked for {track_name}"
                return False, "Track not found in database."
            except Exception as e:
                return False, f"Update failed: {e}"
        except Exception as e:
            logging.error(f"Vault storage critical failure: {e}")
            return False, str(e)
        finally:
            if task_id:
                task_dir = os.path.join(STAGING_DIR, task_id)
                if os.path.exists(task_dir):
                    shutil.rmtree(task_dir, ignore_errors=True)