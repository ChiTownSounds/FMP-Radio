import os
import shutil
import csv
import logging
import threading
import time
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
    _git_lock = threading.Lock()

    def __init__(self):
        self.era_folders = ["Classics", "Old School 70s80s", "Throwbacks 90s2000s", "New School 2010+", "Live", "Unsorted_Review", "intro", "ondemand", "365 Commercials"]

    def _safe_filename(self, name: str) -> str:
        if not name:
            return ""
        return "".join(c for c in name if c not in r'\/:*?"<>|').strip()

    def _normalize_track_key(self, name: str) -> str:
        if not name:
            return ""
        import re
        if " - " in name:
            parts = name.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        else:
            artist = ""
            title = name.strip()
            
        def clean_part(part: str) -> str:
            p = part.lower()
            p = p.replace("’", "'").replace("‘", "'").replace("`", "'")
            # Strip brackets
            p = re.sub(r'\[.*?\]', '', p)
            # Strip parentheses
            p = re.sub(r'\(.*?\)', '', p)
            # Strip feat and everything after it
            p = re.sub(r'\b(feat\.?|featuring|f/)\b.*', '', p)
            # Strip non-alphanumeric
            p = re.sub(r'[^a-z0-9]', '', p)
            return p.strip()
            
        clean_artist = clean_part(artist)
        clean_title = clean_part(title)
        return clean_artist + clean_title

    def _get_rclone_path(self):
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rclone.exe")

    def _git_auto_push(self, track_name: str):
        """Helper to push vaulted updates to GitHub in a background thread."""
        import subprocess
        with self._git_lock:
            try:
                logging.info(f"[*] Starting Auto-Git Synchronization for '{track_name}'...")
                # 1. Add modified CSV file
                subprocess.run(["git", "add", "configs/fmp_data_7718.csv"], check=True, capture_output=True)
                # 2. Commit change
                commit_msg = f"Vaulted new track: {track_name}"
                subprocess.run(["git", "commit", "-m", commit_msg], check=True, capture_output=True)
                # 3. Push to origin main
                subprocess.run(["git", "push", "origin", "main"], check=True, capture_output=True)
                logging.info(f"[✓] Auto-Git Sync Successful! '{track_name}' synced to GitHub.")
            except Exception as e:
                logging.error(f"[-] Auto-Git Sync Failed: {e}")

    def find_candidates(self, query: str) -> List[Dict]:
        """
        Searches the local master CSV database using fuzzy text matching.
        Aggressively strips out and ignores malformed rows to prevent NoneType sorting crashes.
        """
        if not query or not isinstance(query, str):
            query = ""
        query_clean = query.lower().strip()
        
        with self._csv_lock:
            if not os.path.exists(CSV_BLUEPRINT): return []
            
            db = {}
            try:
                with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row and isinstance(row, dict):
                            track_name = row.get('Track Name')
                            if track_name and str(track_name).strip():
                                db[str(track_name).strip()] = row
            except Exception as e:
                logging.error(f"Error reading CSV in find_candidates: {e}")
                return []
            
            if not db: return []

            results_dict = {}
            # Direct text inclusion mapping
            for track_name, row in db.items():
                if query_clean in track_name.lower():
                    data = row.copy()
                    data['name'] = track_name
                    data['score'] = 100.0
                    results_dict[track_name] = data
            
            # Enforce strict string conversion on choices to insulate thefuzz from NoneTypes
            choices = [str(k) for k in db.keys() if k]
            
            if choices and query_clean:
                try:
                    matches = process.extract(query, choices, limit=None)
                    for match_name, score in matches:
                        if score is not None and float(score) >= 70.0 and match_name not in results_dict:
                            data = db[match_name].copy()
                            data['name'] = match_name
                            data['score'] = float(score)
                            results_dict[match_name] = data
                except Exception as e:
                    logging.error(f"Fuzzy matching sequence failed: {e}")

            sorted_results = list(results_dict.values())
            for r in sorted_results:
                if 'score' not in r or r['score'] is None:
                    r['score'] = 0.0

            sorted_results.sort(key=lambda x: float(x.get('score', 0.0)), reverse=True)
            return sorted_results

    def scrub_track(self, exact_title: str) -> Tuple[bool, str]:
        attempts = 3
        delay = 2
        server_deleted = False
        target_filename = f"{exact_title}.mp3"
        rclone_path = self._get_rclone_path()
        import subprocess
        
        # Look up file path in CSV first under lock
        file_path_on_server = None
        with self._csv_lock:
            if os.path.exists(CSV_BLUEPRINT):
                try:
                    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            if row.get('Track Name') == exact_title:
                                file_path_on_server = row.get('File Path')
                                break
                except Exception as e:
                    logging.error(f"Error checking path in CSV for scrub: {e}")

        last_error = ""

        # Attempt optimized deletefile first if path exists
        if file_path_on_server:
            # Clean up Z:/ prefix to map to FTP root
            clean_rel_path = file_path_on_server.replace('\\', '/')
            if clean_rel_path.upper().startswith('Z:/'):
                clean_rel_path = clean_rel_path[3:]
            
            ftp_target_path = f"citrus3:/{clean_rel_path}"
            
            for i in range(attempts):
                try:
                    cmd = [rclone_path, "deletefile", ftp_target_path]
                    subprocess.run(cmd, check=True, capture_output=True)
                    server_deleted = True
                    break
                except subprocess.CalledProcessError as e:
                    err_msg = e.stderr.decode('utf-8', errors='ignore').strip()
                    last_error = f"deletefile failed: {err_msg}"
                    logging.info(f"Direct deletefile failed, retrying: {err_msg}")
                    if i < attempts - 1:
                        time.sleep(delay)
                        delay *= 2

        # Fallback to global --include delete if direct delete didn't succeed
        if not server_deleted:
            escaped_filename = target_filename.replace('[', '\\[').replace(']', '\\]').replace('*', '\\*').replace('?', '\\?')
            delay = 2
            for i in range(attempts):
                try:
                    # Use Rclone to delete the file globally ignoring case
                    cmd = [rclone_path, "delete", "citrus3:/", "--include", escaped_filename, "--ignore-case"]
                    subprocess.run(cmd, check=True, capture_output=True)
                    server_deleted = True
                    break
                except subprocess.CalledProcessError as e:
                    err_msg = e.stderr.decode('utf-8', errors='ignore').strip()
                    last_error = f"global delete failed: {err_msg}"
                    if i == attempts - 1:
                        logging.error(f"Rclone delete fallback attempt failed: {err_msg}")
                        break
                    time.sleep(delay)
                    delay *= 2

        # If direct delete was attempted or fallback was run, and both encountered real errors (e.g. bad flags or network failure)
        # We abort and do NOT delete from CSV, so the user knows deletion failed and can retry.
        if not server_deleted and last_error and "not found" not in last_error.lower():
            return False, f"FTP server deletion failed: {last_error}"

        found_in_csv = False
        with self._csv_lock:
            rows = []
            if os.path.exists(CSV_BLUEPRINT):
                with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    fieldnames = reader.fieldnames
                    for row in reader:
                        if row.get('Track Name') == exact_title: 
                            found_in_csv = True
                        else: 
                            rows.append(row)
                
                if found_in_csv:
                    with open(CSV_BLUEPRINT, 'w', encoding='utf-8', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)

        # Delete from local G: drive mirror if it exists
        try:
            if file_path_on_server:
                clean_rel_path_g = file_path_on_server.replace('\\', '/')
                if clean_rel_path_g.upper().startswith('Z:/'):
                    clean_rel_path_g = clean_rel_path_g[3:]
                g_drive_base = r"G:\My Drive\FMP MUSIC\BASE\MUSIC"
                g_target_file = os.path.join(g_drive_base, clean_rel_path_g.replace('/', os.sep))
                if os.path.exists(g_target_file):
                    os.remove(g_target_file)
                    logging.info(f"Deleted from G: drive: {g_target_file}")
        except Exception as e:
            logging.error(f"Failed to delete from G: drive mirror: {e}")

        # Clean up local lyrics file if it exists
        lyrics_deleted = False
        try:
            safe_track_name = "".join(c for c in exact_title if c not in r'\/:*?"<>|').strip()
            lyrics_dir = os.path.join(os.path.dirname(CSV_BLUEPRINT), "lyrics")
            txt_filepath = os.path.join(lyrics_dir, f"{safe_track_name}.txt")
            if os.path.exists(txt_filepath):
                os.remove(txt_filepath)
                lyrics_deleted = True
        except Exception as e:
            logging.error(f"Failed to delete lyrics file: {e}")

        # Git push updates if CSV database was modified
        if found_in_csv:
            from config import AUTO_GIT_PUSH
            if AUTO_GIT_PUSH:
                threading.Thread(target=self._git_auto_push, args=(exact_title,), daemon=True).start()

        if not server_deleted and not found_in_csv and not lyrics_deleted:
            return False, "Track not found on FTP server, database, or lyrics folders."
            
        return True, "Sync Completed"

    def _is_video_title(self, title: str) -> bool:
        t = title.lower()
        bad_phrases = ["official video", "music video", "official music video", "video clip", "videoclip", "lyric video", "lyrics video"]
        for phrase in bad_phrases:
            if phrase in t:
                return True
        if "(video)" in t or "[video]" in t:
            return True
        return False

    def store_track(self, file_path: str, metadata: dict, task_id: str = "", target_override: str = None) -> Tuple[bool, str]:
        """Restored V3 storage processing pipeline."""
        try:
            # 1. Derive names fresh every time
            clean_artist = self._safe_filename(metadata.get('artist', 'Unknown Artist'))
            clean_title = self._safe_filename(metadata.get('title', 'Unknown Title'))
            
            # 2. Construct the unique target filename
            target_filename = f"{clean_artist} - {clean_title}.mp3"
            clean_name = target_filename
            track_name = f"{clean_artist} - {clean_title}"
            
            release_year = metadata.get('release_year', 'Unknown')
            new_key = self._normalize_track_key(track_name)
            
            # 3. Verify G: drive is mounted
            g_drive_base = r"G:\My Drive\FMP MUSIC\BASE\MUSIC"
            if not os.path.exists(g_drive_base):
                return False, "Google Drive (G: drive) is not mounted or the music folder is missing. Vaulting aborted."

            # 4. Check for duplicates in CSV database
            with self._csv_lock:
                if os.path.exists(CSV_BLUEPRINT):
                    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            existing_name = row.get('Track Name')
                            if existing_name:
                                if self._normalize_track_key(existing_name) == new_key:
                                    return False, "Duplicate Track Detected in CSV"

            # 5. Check G: drive era folders for duplicate files
            for folder in self.era_folders:
                folder_path = os.path.join(g_drive_base, folder)
                if os.path.exists(folder_path):
                    for f in os.listdir(folder_path):
                        if f.lower().endswith(".mp3"):
                            f_name_without_ext = f[:-4]
                            if self._normalize_track_key(f_name_without_ext) == new_key:
                                return False, f"Duplicate Track Detected on G Drive: {folder}/{f}"

            # 6. Determine Era Folder
            if self._is_video_title(track_name) or self._is_video_title(clean_name):
                era_folder = "Unsorted_Review"
                remote_target = "/Unsorted_Review"
            elif target_override: 
                era_folder = target_override
                remote_target = f"/{target_override}"
            else:
                if "live" in clean_name.lower():
                    era_folder = "Live"
                elif not release_year or str(release_year).lower() == 'unknown': 
                    era_folder = "Unsorted_Review"
                else:
                    try:
                        year_int = int(str(release_year)[:4])
                        if year_int < 1970: era_folder = "Classics"
                        elif 1970 <= year_int <= 1989: era_folder = "Old School 70s80s"
                        elif 1990 <= year_int <= 2009: era_folder = "Throwbacks 90s2000s"
                        else: era_folder = "New School 2010+"
                    except:
                        era_folder = "Unsorted_Review"
                remote_target = f"/{era_folder}"

            # 7. Vault to G: drive first (source of truth)
            g_target_dir = os.path.join(g_drive_base, era_folder)
            os.makedirs(g_target_dir, exist_ok=True)
            g_target_file = os.path.join(g_target_dir, clean_name)
            try:
                shutil.copy2(file_path, g_target_file)
                logging.info(f"[✓] Track successfully vaulted to G: drive: {clean_name}")
            except Exception as e:
                return False, f"Failed to write file to G: drive: {e}"

            # 8. Read/Analyze and update metadata using the vaulted file on G: drive
            try:
                audio = MP3(g_target_file)
                length_str = f"{int(audio.info.length // 60)}:{int(audio.info.length % 60):02d}" if audio else "0:00"
                duration_ms = int(round(audio.info.length * 1000)) if audio else 210000

                # Read existing physical file headers to prevent structural drift crashes
                fieldnames = [
                    'Track Name', 'File Path', 'Source_URL', 'duration_ms', 'item_type',
                    'energy_category', 'Intro_Duration', 'Punch_Ms', 'outro_duration', 'bpm',
                    'Bitrate', 'Lyrics', 'Year', 'Art Ratio', 'Length'
                ]
                with self._csv_lock:
                    if os.path.exists(CSV_BLUEPRINT):
                        with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                            r = csv.reader(f)
                            existing_headers = next(r, None)
                        
                        if existing_headers:
                            # Let's ensure all required columns are in existing_headers
                            missing_cols = [c for c in fieldnames if c not in existing_headers]
                            if missing_cols:
                                new_headers = existing_headers + missing_cols
                                rows = []
                                with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                                    reader = csv.DictReader(f)
                                    for row in reader:
                                        rows.append(row)
                                
                                with open(CSV_BLUEPRINT, 'w', encoding='utf-8', newline='') as f:
                                    writer = csv.DictWriter(f, fieldnames=new_headers)
                                    writer.writeheader()
                                    writer.writerows(rows)
                                existing_headers = new_headers
                            
                            fieldnames = existing_headers

                new_row = {}
                for field in fieldnames:
                    lower_field = field.lower()
                    if field == 'Track Name': 
                        new_row[field] = track_name
                    elif field == 'File Path':
                        new_row[field] = f"Z:/{era_folder}/{clean_name}"
                    elif lower_field in ['source_url', 'url', 'source url']: 
                        new_row[field] = metadata.get('url', "")
                    elif field == 'duration_ms':
                        new_row[field] = duration_ms
                    elif field == 'item_type':
                        new_row[field] = metadata.get('item_type', 'Music')
                    elif field == 'energy_category':
                        new_row[field] = metadata.get('energy_category', 'Unassigned')
                    elif field == 'Intro_Duration':
                        new_row[field] = int(metadata.get('intro_duration', 0))
                    elif field == 'Punch_Ms':
                        new_row[field] = int(metadata.get('punch_ms', 2000))
                    elif field == 'outro_duration':
                        new_row[field] = int(metadata.get('outro_duration', 0))
                    elif field == 'bpm':
                        new_row[field] = int(metadata.get('bpm', 0))
                    elif field == 'Bitrate': 
                        new_row[field] = metadata.get('bitrate', '320k')
                    elif field == 'Lyrics': 
                        lyrics_val = metadata.get('lyrics', 'Not Found')
                        if lyrics_val and lyrics_val.strip() not in ['Not Found', 'Unknown', '', 'False']:
                            new_row[field] = 'True'
                            try:
                                safe_track_name = "".join(c for c in track_name if c not in r'\/:*?"<>|').strip()
                                lyrics_dir = os.path.join(os.path.dirname(CSV_BLUEPRINT), "lyrics")
                                os.makedirs(lyrics_dir, exist_ok=True)
                                txt_filepath = os.path.join(lyrics_dir, f"{safe_track_name}.txt")
                                with open(txt_filepath, 'w', encoding='utf-8') as lyrics_file:
                                    lyrics_file.write(lyrics_val)
                            except Exception as e:
                                logging.error(f"Failed to write ingested lyrics file: {e}")
                        else:
                            new_row[field] = 'Unknown'
                    elif lower_field in ['year', 'true_year', 'true year', 'release_year', 'release year']: 
                        new_row[field] = release_year
                    elif lower_field in ['art ratio', 'art_ratio']: 
                        new_row[field] = metadata.get('art_ratio', '1.0')
                    elif field == 'Length': 
                        new_row[field] = length_str
                    elif field == 'Energy Category':
                        new_row[field] = metadata.get('energy_category', metadata.get('energy category', 'Unassigned'))
                    elif field == 'Intro Sec':
                        intro_dur = metadata.get('intro_duration', 0)
                        if intro_dur:
                            new_row[field] = round(intro_dur / 1000.0, 2)
                        else:
                            new_row[field] = metadata.get('intro_sec', 0.0)
                    else: 
                        new_row[field] = metadata.get(lower_field, "")

                with self._csv_lock:
                    file_exists = os.path.exists(CSV_BLUEPRINT)
                    with open(CSV_BLUEPRINT, 'a', encoding='utf-8', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        if not file_exists: writer.writeheader()
                        writer.writerow(new_row)

                # Trigger Git Auto Push if enabled in configuration
                from config import AUTO_GIT_PUSH
                if AUTO_GIT_PUSH:
                    threading.Thread(target=self._git_auto_push, args=(track_name,), daemon=True).start()

                # 9. Remote FTP Upload (Citrus3) as the absolute last step
                import subprocess
                rclone_path = self._get_rclone_path()
                try:
                    cmd = [rclone_path, "copyto", "--inplace", g_target_file, f"citrus3:{remote_target}/{clean_name}"]
                    subprocess.run(cmd, check=True, capture_output=True)
                    logging.info(f"[✓] Track successfully uploaded to Citrus3 FTP (Z:): {clean_name}")
                except subprocess.CalledProcessError as e:
                    return False, f"Rclone FTP Upload Failure from G: drive source: {e.stderr.decode('utf-8', errors='ignore')}"

                return True, "Success"
            except Exception as e:
                return False, f"Database Write Failure: {e}"
        except Exception as e:
            return False, str(e)
        finally:
            if task_id:
                task_dir = os.path.join(STAGING_DIR, task_id)
                if os.path.exists(task_dir):
                    shutil.rmtree(task_dir, ignore_errors=True)