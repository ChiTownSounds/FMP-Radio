import os
import shutil
import csv
import logging
import threading
import time
import subprocess
import re
from typing import List, Tuple, Dict
from thefuzz import process 
from mutagen.mp3 import MP3

from config import CSV_BLUEPRINT, STAGING_DIR, FTP_HOST, FTP_USER, FTP_PASS, FTP_BASE_DIR, FTP_PORT

def clean_version_tags(text: str) -> str:
    if not text:
        return ""
    import re
    # 1. Strip parenthesized/bracketed exact version phrases (case-insensitive)
    text = re.sub(r'\s*[([]\s*(?:clean|explicit|radio edit|radio version|album version|explicit version|clean version|clean edit|explicit edit)\s*[\])]', '', text, flags=re.I)
    # 2. Strip trailing version suffixes after hyphens
    text = re.sub(r'\s*-\s*(?:clean|explicit|radio edit|radio version|album version|explicit version|clean version|clean edit|explicit edit)\b', '', text, flags=re.I)
    # 3. Strip trailing standalone version words
    text = re.sub(r'\s+(?:clean|explicit|radio edit|radio version)\s*$', '', text, flags=re.I)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

class TransmissionError(Exception):
    """Custom exception for FTP transmission failures."""
    pass

class VaultManager:
    # Class-level lock to ensure all instances of VaultManager synchronize CSV access
    _csv_lock = threading.RLock()
    _git_lock = threading.Lock()

    def __init__(self):
        self.era_folders = ["Classics", "Old School 70s80s", "Throwbacks 90s2000s", "New School 2010+", "Live", "Unsorted_Review", "intro", "ondemand", "365 Commercials"]

    def _safe_filename(self, name: str) -> str:
        if not name:
            return ""
        return "".join(c for c in name if c not in r'\/:*?"<>|').strip()

    def _normalize_track_key(self, name: str, explicit_val=None) -> str:
        if not name:
            return ""
        import re
        import unicodedata
        
        name_lower = name.lower()
        
        # Determine version category
        is_radio = 'radio edit' in name_lower or 'radio version' in name_lower
        
        if explicit_val is not None:
            is_explicit = explicit_val in [True, 1, 'true', '1', 'True'] or ('explicit' in name_lower and 'clean' not in name_lower)
        else:
            is_explicit = 'explicit' in name_lower and 'clean' not in name_lower
            
        # Decompose accents/diacritics and convert to ASCII (e.g. Ÿ -> Y, ‐ -> -)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        
        parts = name.split(' - ', 1)
        if len(parts) == 2:
            artist, title = parts
        else:
            artist = ""
            title = name
            
        # Clean artist: only keep first artist before feat
        artist_clean = artist.lower()
        artist_clean = re.split(r'\s+(feat\.?|featuring|with|w/|f/|and|&)\s+', artist_clean)[0]
        artist_clean = re.sub(r'[^a-z0-9]', '', artist_clean)
        
        # Clean title: strip brackets and parenthesized features/version details
        title_clean = title.lower()
        title_clean = re.sub(r'\[.*?\]', '', title_clean)
        title_clean = re.sub(r'\(.*?\)', '', title_clean)
        removals = ["radio edit", "single mix", "album version", "rerecorded", "clean", "explicit", "remix"]
        for r in removals:
            title_clean = title_clean.replace(r, "")
        title_clean = re.sub(r'[^a-z0-9]', '', title_clean)
        
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
            
        base_key = f"{artist_clean}_{title_clean}{variant_suffix}"
        if is_radio:
            return f"{base_key}_radioedit"
        elif is_explicit:
            return f"{base_key}_explicit"
        else:
            return f"{base_key}_clean"

    def _get_rclone_path(self):
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

    def _git_auto_push(self, track_name: str):
        """Helper to push vaulted updates to GitHub in a background thread with retries."""
        import time
        import os
        from config import git_operation_lock
        from filelock import Timeout as _LockTimeout

        for attempt in range(5):
            lock_acquired = False
            try:
                # Cross-process lock (config.git_operation_lock) shared with
                # every other git-touching site in this codebase - a prior
                # hand-rolled O_CREAT|O_EXCL lock file here only protected
                # against other callers using that exact same mechanism.
                # tools/sync_library_db.py's cron-invoked git pull/push never
                # touched it, so the two could (and did, 2026-09-03) run
                # `git pull --rebase` at the same moment and produce a real,
                # stuck merge conflict with literal <<<<<<< markers baked
                # into the live CSV for ~50 minutes before anything noticed.
                try:
                    lock_ctx = git_operation_lock(timeout=10)
                    lock_ctx.acquire()
                    lock_acquired = True
                except _LockTimeout:
                    logging.warning(f"[Git Sync] Could not acquire git lock. Retrying attempt {attempt+1}/5...")
                    time.sleep(1 + attempt)
                    continue

                # Synchronize with self._git_lock for in-process safety
                with self._git_lock:
                    logging.info(f"[*] Starting Auto-Git Synchronization for '{track_name}' (attempt {attempt+1})...")
                    
                    # Clean up any stale index.lock file in our repo if it exists
                    git_index_lock = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".git", "index.lock")
                    if os.path.exists(git_index_lock):
                        try:
                            os.remove(git_index_lock)
                            logging.warning("[Git Sync] Removed stale git index.lock file.")
                        except Exception as lock_err:
                            logging.warning(f"[Git Sync] Could not remove index.lock: {lock_err}")
                            
                    # Get current branch name dynamically
                    res_branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, check=True)
                    branch_name = res_branch.stdout.strip()

                    # 1. Add modified CSV file
                    subprocess.run(["git", "add", "configs/fmp_data_7718.csv"], check=True, capture_output=True)

                    # Check if there are any staged changes for the CSV file
                    status_res = subprocess.run(["git", "status", "--porcelain", "configs/fmp_data_7718.csv"], capture_output=True, text=True, check=True)
                    if status_res.stdout.strip():
                        # 2. Commit change - pathspec-restricted so this NEVER sweeps up
                        # whatever else happens to be staged by other work (e.g. an
                        # interactive git add mid-development). A prior unrestricted
                        # `git commit -m ...` here silently committed and pushed
                        # unrelated in-progress files more than once.
                        commit_msg = f"Vaulted new track: {track_name}"
                        commit_res = subprocess.run(["git", "commit", "configs/fmp_data_7718.csv", "-m", commit_msg], capture_output=True, text=True)
                        if commit_res.returncode != 0:
                            stdout_lower = commit_res.stdout.lower()
                            stderr_lower = commit_res.stderr.lower()
                            clean_messages = ["nothing to commit", "working tree clean", "no changes added to commit", "nothing added to commit"]
                            if not any(msg in stdout_lower or msg in stderr_lower for msg in clean_messages):
                                # Real commit failure
                                raise Exception(f"git commit failed with code {commit_res.returncode}: {commit_res.stderr or commit_res.stdout}")
                            else:
                                logging.info("[Git Sync] Commit skipped (clean message match). Proceeding to pull and push.")
                    else:
                        logging.info("[Git Sync] CSV database file has no changes to commit. Proceeding to pull and push.")

                    # 3. Pull + rebase with --autostash: git's own stash/pop handling
                    # for any unrelated unstaged changes, instead of a hand-rolled
                    # stash/pop where a failed pop was silently swallowed (check=False)
                    # and left the stash orphaned forever - the source of a large pile
                    # of abandoned "WIP on dev" stashes found in this repo.
                    subprocess.run(["git", "pull", "--rebase", "--autostash", "origin", branch_name], check=True, capture_output=True)
                    # 4. Push to origin branch
                    subprocess.run(["git", "push", "origin", branch_name], check=True, capture_output=True)

                    logging.info(f"[✓] Auto-Git Sync Successful! '{track_name}' synced to GitHub.")
                    return # Success!
            except Exception as e:
                logging.error(f"[-] Auto-Git Sync Attempt {attempt+1} Failed: {e}")
                time.sleep(1 + attempt * 2)
            finally:
                if lock_acquired:
                    try:
                        lock_ctx.release()
                    except:
                        pass

        logging.error(f"[FATAL] Auto-Git Sync completely failed for '{track_name}' after 5 attempts.")

    def find_candidates(self, query: str) -> List[Dict]:
        """
        Searches the local master CSV database using fuzzy text matching.
        Returns all matching candidate entries (including clean/explicit duplicates).
        """
        if not query or not isinstance(query, str):
            query = ""
        query_clean = query.lower().strip()
        
        with self._csv_lock:
            if not os.path.exists(CSV_BLUEPRINT): return []
            
            db_rows = []
            track_names = set()
            try:
                with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row and isinstance(row, dict):
                            track_name = row.get('Track Name')
                            if track_name and str(track_name).strip():
                                db_rows.append(row)
                                track_names.add(str(track_name).strip())
            except Exception as e:
                logging.error(f"Error reading CSV in find_candidates: {e}")
                return []
            
            if not track_names: return []

            results_list = []
            matched_track_names = {}
            
            # 1. Direct text inclusion matching
            for t_name in track_names:
                if query_clean in t_name.lower():
                    matched_track_names[t_name] = 100.0
            
            # 2. Fuzzy matching
            choices = list(track_names)
            if choices and query_clean:
                try:
                    matches = process.extract(query, choices, limit=None)
                    for match_name, score in matches:
                        if score is not None and float(score) >= 70.0:
                            if match_name not in matched_track_names or float(score) > matched_track_names[match_name]:
                                matched_track_names[match_name] = float(score)
                except Exception as e:
                    logging.error(f"Fuzzy matching sequence failed: {e}")

            # 3. Collect all matching rows (resolving duplicates)
            for row in db_rows:
                t_name = row.get('Track Name')
                if t_name in matched_track_names:
                    data = row.copy()
                    data['name'] = t_name
                    
                    is_expl = str(row.get('Explicit', 'False')).strip().lower() in ['true', '1']
                    path_lower = (row.get('File Path') or "").lower()
                    is_radio = 'radio edit' in path_lower or 'radio version' in path_lower or 'radioedit' in path_lower
                    
                    if is_radio:
                        display_version = "Radio Edit"
                    elif is_expl:
                        display_version = "Explicit"
                    else:
                        display_version = "Clean"
                        
                    data['display_version'] = display_version
                    data['score'] = matched_track_names[t_name]
                    results_list.append(data)
                    
            results_list.sort(key=lambda x: (float(x.get('score', 0.0)), x.get('display_version', '')), reverse=True)
            return results_list

    def scrub_track(self, target_identifier: str) -> Tuple[bool, str]:
        """
        Deletes a track. target_identifier can be a Track Name (scrubs first found file)
        or a File Path (scrubs that specific version, leaving others intact).
        """
        attempts = 3
        delay = 2
        server_deleted = False
        rclone_path = self._get_rclone_path()
        
        is_path = '/' in target_identifier or '\\' in target_identifier or target_identifier.endswith('.mp3')
        file_path_on_server = None
        exact_title = None
        
        with self._csv_lock:
            if os.path.exists(CSV_BLUEPRINT):
                try:
                    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            row_title = row.get('Track Name')
                            row_path = row.get('File Path')
                            if is_path:
                                if row_path and row_path.replace('\\', '/').lower() == target_identifier.replace('\\', '/').lower():
                                    file_path_on_server = row_path
                                    exact_title = row_title
                                    break
                            else:
                                if row_title == target_identifier:
                                    file_path_on_server = row_path
                                    exact_title = row_title
                                    break
                except Exception as e:
                    logging.error(f"Error checking path in CSV for scrub: {e}")

        if not file_path_on_server:
            exact_title = target_identifier
            
        last_error = ""

        file_path_to_delete = file_path_on_server or (target_identifier if is_path else None)
        if file_path_to_delete:
            # Clean up prefix to map to FTP root
            clean_rel_path = file_path_to_delete.replace('\\', '/')
            if clean_rel_path.upper().startswith('Z:/'):
                clean_rel_path = clean_rel_path[3:]
            elif clean_rel_path.lower().startswith('/home/ubuntu/music/'):
                clean_rel_path = clean_rel_path[len('/home/ubuntu/music/'):]
            
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

        # Fallback to global filename-based delete if direct deletefile failed AND we don't have a path
        if not server_deleted and not is_path:
            target_filename = f"{exact_title}.mp3"
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

        # If direct delete was attempted or fallback was run, and both encountered real errors
        if not server_deleted and last_error and "not found" not in last_error.lower() and "doesn't exist" not in last_error.lower():
            return False, f"FTP server deletion failed: {last_error}"

        found_in_csv = False
        with self._csv_lock:
            rows = []
            if os.path.exists(CSV_BLUEPRINT):
                with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    fieldnames = reader.fieldnames
                    for row in reader:
                        row_title = row.get('Track Name')
                        row_path = row.get('File Path')
                        
                        match = False
                        if is_path:
                            if row_path and row_path.replace('\\', '/').lower() == target_identifier.replace('\\', '/').lower():
                                match = True
                        else:
                            if row_title == target_identifier:
                                match = True
                                
                        if match:
                            found_in_csv = True
                        else: 
                            rows.append(row)
                
                if found_in_csv:
                    with open(CSV_BLUEPRINT, 'w', encoding='utf-8', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)

        db_deleted = False
        try:
            from config import BROADCASTER_DB
            import sqlite3
            if os.path.exists(BROADCASTER_DB):
                conn = sqlite3.connect(BROADCASTER_DB, timeout=10.0)
                cursor = conn.cursor()
                db_rel_path = file_path_on_server or (target_identifier if is_path else None)
                if db_rel_path:
                    db_rel_path = db_rel_path.replace('\\', '/')
                    if db_rel_path.upper().startswith('Z:/'):
                        db_rel_path = db_rel_path[3:]
                    elif db_rel_path.lower().startswith('/home/ubuntu/music/'):
                        db_rel_path = db_rel_path[len('/home/ubuntu/music/'):]
                    
                    if is_path:
                        cursor.execute("DELETE FROM media_library WHERE LOWER(file_path) = LOWER(?)", (db_rel_path,))
                    else:
                        if " - " in target_identifier:
                            artist_part, title_part = target_identifier.split(" - ", 1)
                            cursor.execute("DELETE FROM media_library WHERE (LOWER(title) = LOWER(?) AND LOWER(artist) = LOWER(?)) OR LOWER(file_path) = LOWER(?)", (title_part.strip(), artist_part.strip(), db_rel_path))
                        else:
                            cursor.execute("DELETE FROM media_library WHERE LOWER(title) = LOWER(?) OR LOWER(file_path) = LOWER(?)", (target_identifier, db_rel_path))
                else:
                    if " - " in target_identifier:
                        artist_part, title_part = target_identifier.split(" - ", 1)
                        cursor.execute("DELETE FROM media_library WHERE LOWER(title) = LOWER(?) AND LOWER(artist) = LOWER(?)", (title_part.strip(), artist_part.strip()))
                    else:
                        cursor.execute("DELETE FROM media_library WHERE LOWER(title) = LOWER(?)", (target_identifier,))
                
                if cursor.rowcount > 0:
                    db_deleted = True
                    conn.commit()
                    logging.info(f"Deleted track from local Broadcaster DB: {target_identifier}")
                conn.close()
        except Exception as e:
            logging.error(f"Failed to delete track from local Broadcaster DB: {e}")

        remote_deleted = False
        try:
            try:
                from config import REMOTE_VM_IP
            except ImportError:
                REMOTE_VM_IP = "ultimate.fmpmediagroup.com"
                
            ssh_key = "C:\\Users\\chito\\.ssh\\id_ed25519"
            if not os.path.exists(ssh_key):
                ssh_key = os.path.expanduser("~/.ssh/id_ed25519")
                
            if os.path.exists(ssh_key) and REMOTE_VM_IP:
                db_rel_path = file_path_on_server or (target_identifier if is_path else None)
                if db_rel_path:
                    db_rel_path = db_rel_path.replace('\\', '/')
                    if db_rel_path.upper().startswith('Z:/'):
                        db_rel_path = db_rel_path[3:]
                    elif db_rel_path.lower().startswith('/home/ubuntu/music/'):
                        db_rel_path = db_rel_path[len('/home/ubuntu/music/'):]
                        
                    db_rel_path_esc = db_rel_path.replace("'", "''")
                    if is_path:
                        sql = f"DELETE FROM media_library WHERE LOWER(file_path) = LOWER('{db_rel_path_esc}');"
                    else:
                        if " - " in target_identifier:
                            artist_part, title_part = target_identifier.split(" - ", 1)
                            artist_esc = artist_part.strip().replace("'", "''")
                            title_esc = title_part.strip().replace("'", "''")
                            sql = f"DELETE FROM media_library WHERE (LOWER(title) = LOWER('{title_esc}') AND LOWER(artist) = LOWER('{artist_esc}')) OR LOWER(file_path) = LOWER('{db_rel_path_esc}');"
                        else:
                            target_esc = target_identifier.replace("'", "''")
                            sql = f"DELETE FROM media_library WHERE LOWER(title) = LOWER('{target_esc}') OR LOWER(file_path) = LOWER('{db_rel_path_esc}');"
                else:
                    if " - " in target_identifier:
                        artist_part, title_part = target_identifier.split(" - ", 1)
                        artist_esc = artist_part.strip().replace("'", "''")
                        title_esc = title_part.strip().replace("'", "''")
                        sql = f"DELETE FROM media_library WHERE LOWER(title) = LOWER('{title_esc}') AND LOWER(artist) = LOWER('{artist_esc}');"
                    else:
                        target_esc = target_identifier.replace("'", "''")
                        sql = f"DELETE FROM media_library WHERE LOWER(title) = LOWER('{target_esc}');"
                
                # The SQL string is piped via stdin rather than embedded in the
                # remote command string - the previous version interpolated it
                # inside a double-quoted shell string, which only SQL-escapes
                # single quotes and does nothing to stop the remote bash from
                # expanding $(...), backticks, or $VAR in a crafted track
                # title/artist. Sending it over stdin means no shell (local or
                # remote) ever parses the SQL text at all.
                ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-i", ssh_key, f"ubuntu@{REMOTE_VM_IP}", "sqlite3 /home/ubuntu/FMP-Broadcaster/fmp_radio.db"]
                res = subprocess.run(ssh_cmd, input=sql, capture_output=True, text=True, timeout=10)
                if res.returncode == 0:
                    logging.info(f"Deleted track from remote Broadcaster DB: {target_identifier}")
                    remote_deleted = True
                else:
                    logging.error(f"Failed to delete from remote Broadcaster DB: {res.stderr}")
        except Exception as e:
            logging.error(f"Failed to delete track from remote Broadcaster DB: {e}")

        try:
            file_path_for_g = file_path_on_server or (target_identifier if is_path else None)
            if file_path_for_g:
                clean_rel_path_g = file_path_for_g.replace('\\', '/')
                if clean_rel_path_g.upper().startswith('Z:/'):
                    clean_rel_path_g = clean_rel_path_g[3:]
                elif clean_rel_path_g.lower().startswith('/home/ubuntu/music/'):
                    clean_rel_path_g = clean_rel_path_g[len('/home/ubuntu/music/'):]
                
                from config import MUSIC_DIR
                g_drive_base = MUSIC_DIR
                g_target_file = os.path.join(g_drive_base, clean_rel_path_g.replace('/', os.sep))
                if os.path.exists(g_target_file):
                    os.remove(g_target_file)
                    logging.info(f"Deleted from local storage: {g_target_file}")
                
                # Delete from remote Google Drive if on Linux
                if os.name != "nt":
                    gdrive_target = f"gdrive:FMP MUSIC/BASE/MUSIC/{clean_rel_path_g}"
                    cmd_delete = [rclone_path, "deletefile", gdrive_target]
                    subprocess.run(cmd_delete, check=False, capture_output=True)
                    logging.info(f"Deleted from remote Google Drive: {gdrive_target}")
                else:
                    # On Windows the local G: drive delete above propagates UP to
                    # Google Drive on its own (G: drive IS the Google Drive desktop
                    # sync mount) - but nothing syncs that deletion back DOWN to the
                    # VM's separate local disk copy at /home/ubuntu/music. The
                    # every-3-minute cron sync there is `rclone copy`, additive-only
                    # - it pulls new/changed files but never removes local files
                    # that vanished from the source, so an erased track's bytes
                    # would otherwise sit orphaned on the VM's disk forever (and
                    # could get silently re-imported by a future untracked-file
                    # sweep). Explicitly delete the VM-side file over SSH instead.
                    try:
                        from config import REMOTE_VM_IP as _REMOTE_VM_IP
                    except ImportError:
                        _REMOTE_VM_IP = "ultimate.fmpmediagroup.com"
                    _ssh_key = "C:\\Users\\chito\\.ssh\\id_ed25519"
                    if not os.path.exists(_ssh_key):
                        _ssh_key = os.path.expanduser("~/.ssh/id_ed25519")
                    if os.path.exists(_ssh_key) and _REMOTE_VM_IP:
                        remote_path = f"/home/ubuntu/music/{clean_rel_path_g}"
                        # Path piped via stdin to a remote python3 one-liner, same
                        # pattern as the SQL delete above - no shell (local or
                        # remote) ever parses the path text, so it stays safe
                        # regardless of what characters are in the track's filename.
                        remote_rm_cmd = [
                            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-i", _ssh_key,
                            f"ubuntu@{_REMOTE_VM_IP}",
                            "python3 -c \"import sys, os; p = sys.stdin.read(); os.path.exists(p) and os.remove(p)\""
                        ]
                        res_rm = subprocess.run(remote_rm_cmd, input=remote_path, capture_output=True, text=True, timeout=10)
                        if res_rm.returncode == 0:
                            logging.info(f"Deleted from remote VM disk: {remote_path}")
                        else:
                            logging.error(f"Failed to delete remote VM file {remote_path}: {res_rm.stderr}")
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

        if not server_deleted and not found_in_csv and not lyrics_deleted and not db_deleted and not remote_deleted:
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

    def store_track(self, file_path: str, metadata: dict, task_id: str = "", target_override: str = None, overwrite: bool = False) -> Tuple[bool, str]:
        """Restored V3 storage processing pipeline."""
        try:
            # 1. Derive names fresh every time
            clean_artist = self._safe_filename(metadata.get('artist', 'Unknown Artist'))
            clean_title = self._safe_filename(metadata.get('title', 'Unknown Title'))
            
            # Remove any explicit/clean tags from the artist and title to keep the filename clean
            clean_title_filename = clean_version_tags(clean_title)
            clean_artist_filename = clean_version_tags(clean_artist)
            
            target_filename = f"{clean_artist_filename} - {clean_title_filename}.mp3"
            clean_name = target_filename
            track_name = f"{clean_artist_filename} - {clean_title_filename}"
            
            # Determine version category and folder
            is_explicit = str(metadata.get('explicit', 'False')).strip().lower() in ['true', '1']
            title_lower = clean_title.lower()
            is_radio = metadata.get('is_radio') or 'radio edit' in title_lower or 'radio version' in title_lower
            
            if is_radio:
                version_folder = "Radio Edit"
            elif is_explicit:
                version_folder = "Explicit"
            else:
                version_folder = "Clean"
            
            release_year = metadata.get('release_year', 'Unknown')
            new_key = self._normalize_track_key(track_name, explicit_val=is_explicit)
            
            # 3. Verify music folder/G: drive is mounted
            from config import MUSIC_DIR
            g_drive_base = MUSIC_DIR
            if not os.path.exists(g_drive_base):
                return False, "Music folder (G: drive) is not mounted or missing. Vaulting aborted."

            # 4. Check for duplicates in CSV database
            duplicates_to_scrub = []
            with self._csv_lock:
                if os.path.exists(CSV_BLUEPRINT):
                    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            existing_name = row.get('Track Name')
                            existing_path = row.get('File Path')
                            if existing_name:
                                existing_explicit = row.get('Explicit', '').strip().lower() in ['true', '1']
                                existing_key = self._normalize_track_key(existing_name, explicit_val=existing_explicit)
                                if existing_key == new_key:
                                    if overwrite:
                                        duplicates_to_scrub.append(existing_path or existing_name)
                                    else:
                                        return False, "Duplicate Track Detected in CSV"

            # If overwrite is active and we found duplicates, scrub them cleanly
            if overwrite and duplicates_to_scrub:
                for dup in duplicates_to_scrub:
                    logging.info(f"[*] Overwrite active: Scrubbing duplicate CSV/DB record: {dup}")
                    success, msg = self.scrub_track(dup)
                    if not success:
                        logging.warning(f"[-] Scrubbing of {dup} returned: {msg}")

            # 5. Check G: drive era and flat Music folders for duplicate files
            g_duplicates_to_scrub = []
            for folder in self.era_folders + ["Music"]:
                for subf in ["", "Clean", "Explicit", "Radio Edit"]:
                    if subf:
                        folder_path = os.path.join(g_drive_base, folder, subf)
                    else:
                        folder_path = os.path.join(g_drive_base, folder)
                    
                    if os.path.exists(folder_path):
                        for f in os.listdir(folder_path):
                            if f.lower().endswith(".mp3"):
                                f_name_without_ext = f[:-4]
                                f_explicit = (subf == "Explicit") or ('explicit' in f_name_without_ext.lower())
                                f_key = self._normalize_track_key(f_name_without_ext, explicit_val=f_explicit)
                                if f_key == new_key:
                                    if folder == "Music":
                                        rel_path = f"Music/{f}"
                                    else:
                                        rel_path = f"{folder}/{subf}/{f}" if subf else f"{folder}/{f}"
                                    
                                    if overwrite:
                                        g_duplicates_to_scrub.append(rel_path)
                                    else:
                                        if folder == "Music":
                                            return False, f"Duplicate Track Detected on G Drive: Music/{f}"
                                        else:
                                            return False, f"Duplicate Track Detected on G Drive: {folder}/{subf}/{f}"

            # Scrub files found on G Drive that might not be tracked in the CSV
            if overwrite and g_duplicates_to_scrub:
                for rel_path in g_duplicates_to_scrub:
                    logging.info(f"[*] Overwrite active: Scrubbing physical duplicate on G Drive: {rel_path}")
                    success, msg = self.scrub_track(rel_path)
                    if not success:
                        logging.warning(f"[-] Scrubbing of physical duplicate {rel_path} returned: {msg}")

            # 6. Determine Era Folder
            if self._is_video_title(track_name) or self._is_video_title(clean_name):
                era_folder = "Unsorted_Review"
            elif target_override: 
                era_folder = target_override
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
            
            is_inspirational = (era_folder == "Shows/InspirationalChurch" or era_folder == "InspirationalChurch")
            
            production_folders = ["365 Commercials", "ondemand", "recordings", "intro"]
            is_production = era_folder in production_folders
            is_show = era_folder.startswith("Shows/") or era_folder == "InspirationalChurch"
            if is_show and era_folder == "InspirationalChurch":
                era_folder = "Shows/InspirationalChurch"
            
            if is_production or is_show:
                relative_target_dir = era_folder
                remote_target = f"/{era_folder}"
                relative_file_path = f"{era_folder}/{clean_name}"
            else:
                relative_target_dir = "Music"
                remote_target = "/Music"
                
                # Determine version suffix
                suffix = ""
                if is_radio:
                    suffix = " (Radio Edit)"
                elif is_explicit:
                    suffix = " (Explicit)"
                
                target_filename = f"{clean_artist_filename} - {clean_title_filename}{suffix}.mp3"
                clean_name = target_filename
                relative_file_path = f"Music/{target_filename}"

            # 7. Vault to G: drive first (source of truth)
            g_target_dir = os.path.join(g_drive_base, relative_target_dir)
            os.makedirs(g_target_dir, exist_ok=True)
            g_target_file = os.path.join(g_target_dir, clean_name)
            try:
                shutil.copy2(file_path, g_target_file)
                if is_production:
                    logging.info(f"[✓] Track successfully vaulted to G: drive: {clean_name} under {era_folder}")
                else:
                    logging.info(f"[✓] Track successfully vaulted to G: drive: {clean_name} under Music/")
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
                    'Bitrate', 'Lyrics', 'Year', 'Art Ratio', 'Length', 'Pool', 'Explicit', 'Fingerprint'
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
                        new_row[field] = relative_file_path
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
                    elif field == 'Explicit':
                        explicit_val = metadata.get('explicit')
                        if explicit_val is True:
                            new_row[field] = 'True'
                        elif explicit_val is False:
                            new_row[field] = 'False'
                        else:
                            val_str = str(explicit_val or '').lower()
                            if val_str in ['true', '1', 'yes']:
                                new_row[field] = 'True'
                            elif val_str in ['false', '0', 'no']:
                                new_row[field] = 'False'
                            else:
                                new_row[field] = 'Unknown'
                    elif field == 'Pool':
                        if is_inspirational:
                            new_row[field] = '5'
                        else:
                            new_row[field] = metadata.get('pool', '') or metadata.get('music_pool_id', '')
                    else: 
                        new_row[field] = metadata.get(lower_field, "")

                rclone_path = self._get_rclone_path()

                # 9. Remote FTP Upload (Citrus3) first (Only if NOT explicit)
                is_explicit_val = str(new_row.get('Explicit', 'False')).strip().lower() in ['true', '1']
                is_explicit_by_name = 'explicit' in clean_name.lower() or 'explicit' in relative_file_path.lower()
                
                if is_explicit_val or is_explicit_by_name:
                    logging.info(f"[*] Skipping Citrus3 FTP upload for explicit track: {clean_name}")
                else:
                    try:
                        cmd = [rclone_path, "copyto", g_target_file, f"citrus3:{remote_target}/{clean_name}"]
                        subprocess.run(cmd, check=True, capture_output=True)
                        logging.info(f"[✓] Track successfully uploaded to Citrus3 FTP (Z:): {clean_name}")
                    except subprocess.CalledProcessError as e:
                        return False, f"Rclone FTP Upload Failure to Citrus3: {e.stderr.decode('utf-8', errors='ignore')}"

                # 9.5 Remote Google Drive Mirror Upload (G:) if running on Linux/VM
                if os.name != "nt":
                    try:
                        gdrive_target = f"gdrive:FMP MUSIC/BASE/MUSIC/{relative_file_path}"
                        cmd_gdrive = [rclone_path, "copyto", g_target_file, gdrive_target]
                        subprocess.run(cmd_gdrive, check=True, capture_output=True)
                        logging.info(f"[✓] Track successfully uploaded to Google Drive Mirror (G:): {clean_name}")
                    except subprocess.CalledProcessError as e:
                        logging.error(f"[-] Rclone Google Drive Upload failed: {e.stderr.decode('utf-8', errors='ignore')}")

                # 10. Update local CSV database
                with self._csv_lock:
                    file_exists = os.path.exists(CSV_BLUEPRINT)
                    with open(CSV_BLUEPRINT, 'a', encoding='utf-8', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        if not file_exists: writer.writeheader()
                        writer.writerow(new_row)

                # 11. Trigger Git Auto Push if enabled in configuration
                from config import AUTO_GIT_PUSH
                if AUTO_GIT_PUSH:
                    threading.Thread(target=self._git_auto_push, args=(track_name,), daemon=True).start()

                # Callers that need to transport the file elsewhere after a
                # successful vault (e.g. wait_and_scp, pushing a copy to the
                # VM) previously kept using the original staging path - but
                # this function's own cleanup step (below, task_dir rmtree)
                # deletes that staging file as its last action, so by the
                # time an async transport thread ran, the source was already
                # gone ("No such file or directory"). Exposing the real
                # final path here lets callers use the right file instead.
                self.last_vaulted_path = g_target_file
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