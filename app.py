import sys
import os
import shutil
import subprocess
import logging
import threading
import queue
import time
import itertools
import collections
from datetime import datetime
from flask import Flask, render_template, request, jsonify

from config import STAGING_DIR, SOMEDL_CMD, YT_DLP_CMD, FTP_HOST, FTP_USER, FTP_PASS, FTP_BASE_DIR, FTP_PORT, CSV_BLUEPRINT

# --- CORE MODULE IMPORTS ---
from modules.ingest import Gatekeeper
from modules.download import Transporter
from modules.tagger import AutoMaster
from modules.storage import VaultManager

# --- LOGGING INFRASTRUCTURE ---
# Using dynamic relative paths to match config.py setup
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# Silence werkzeug noise
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# [PHASE G] Persistent Error Logging
error_log_path = os.path.join(LOG_DIR, "error.log")
file_handler = logging.FileHandler(error_log_path)
file_handler.setLevel(logging.ERROR)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logging.getLogger().addHandler(file_handler)

app = Flask(__name__)

class UrlQueue:
    def __init__(self):
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.items = []
        self.counter = 0

    def _save_to_disk(self):
        import json
        try:
            queue_file = os.path.join(BASE_DIR, "configs", "saved_queue.json")
            with open(queue_file, "w", encoding="utf-8") as f:
                json.dump(self.items, f, indent=4)
        except Exception as e:
            sys.stdout.write(f"\n[ERROR] Failed to save queue to disk: {e}\n")
            sys.stdout.flush()

    def put(self, item):
        with self.lock:
            self.counter += 1
            item = item.copy()
            item['id'] = self.counter
            self.items.append(item)
            self._save_to_disk()
            self.cond.notify()
            return item['id']

    def get(self, timeout=None):
        with self.lock:
            if not self.items:
                if timeout is not None:
                    self.cond.wait(timeout)
                    if not self.items:
                        raise queue.Empty
                else:
                    while not self.items:
                        self.cond.wait()
            item = self.items.pop(0)
            self._save_to_disk()
            return item

    def remove(self, item_id):
        with self.lock:
            for i, item in enumerate(self.items):
                if item.get('id') == item_id:
                    self.items.pop(i)
                    self._save_to_disk()
                    self.cond.notify_all()
                    return True
            return False

    def qsize(self):
        with self.lock:
            return len(self.items)

    def empty(self):
        with self.lock:
            return len(self.items) == 0

    def task_done(self):
        pass

    def get_all(self):
        with self.lock:
            return [dict(item) for item in self.items]

class SystemState:
    def __init__(self):
        self.url_queue = UrlQueue()
        self.vault_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.downloader_thread = None
        self.vault_thread = None
        self.total_in_queue = 0
        self.completed_count = 0
        self.current_status = "Idle"
        self.current_track_url = "None"
        self.logs = collections.deque(["FMP ULTIMATE ONLINE. AUTO-MASTERING ACTIVE."], maxlen=15)
        self.boot_cleared = False
        self.last_stat_update = 0
        self.vault_total = 0
        self.folder_breakdown = {}
        self.pending_iheart_queue = []
        self.rejected_iheart = []
        self.load_pending()
        self.load_rejected()
        self.load_saved_queue()
        self.start_spinner()

    def load_pending(self):
        import json
        configs_dir = os.path.join(BASE_DIR, "configs")
        os.makedirs(configs_dir, exist_ok=True)
        pending_file = os.path.join(configs_dir, "pending_iheart.json")
        if os.path.exists(pending_file):
            try:
                with open(pending_file, "r", encoding="utf-8") as f:
                    self.pending_iheart_queue = json.load(f)
            except Exception as e:
                self.log(f"[ERROR] Failed to load pending discoveries: {e}")
                self.pending_iheart_queue = []
        else:
            self.pending_iheart_queue = []

    def save_pending(self):
        import json
        configs_dir = os.path.join(BASE_DIR, "configs")
        os.makedirs(configs_dir, exist_ok=True)
        pending_file = os.path.join(configs_dir, "pending_iheart.json")
        try:
            with open(pending_file, "w", encoding="utf-8") as f:
                json.dump(self.pending_iheart_queue, f, indent=4)
        except Exception as e:
            self.log(f"[ERROR] Failed to save pending discoveries: {e}")

    def load_rejected(self):
        import json
        configs_dir = os.path.join(BASE_DIR, "configs")
        os.makedirs(configs_dir, exist_ok=True)
        rejected_file = os.path.join(configs_dir, "rejected_iheart.json")
        if os.path.exists(rejected_file):
            try:
                with open(rejected_file, "r", encoding="utf-8") as f:
                    self.rejected_iheart = json.load(f)
            except Exception as e:
                self.log(f"[ERROR] Failed to load rejected discoveries: {e}")
                self.rejected_iheart = []
        else:
            self.rejected_iheart = []

    def save_rejected(self):
        import json
        configs_dir = os.path.join(BASE_DIR, "configs")
        os.makedirs(configs_dir, exist_ok=True)
        rejected_file = os.path.join(configs_dir, "rejected_iheart.json")
        try:
            with open(rejected_file, "w", encoding="utf-8") as f:
                json.dump(self.rejected_iheart, f, indent=4)
        except Exception as e:
            self.log(f"[ERROR] Failed to save rejected discoveries: {e}")

    def load_saved_queue(self):
        import json
        queue_file = os.path.join(BASE_DIR, "configs", "saved_queue.json")
        if os.path.exists(queue_file):
            try:
                with open(queue_file, "r", encoding="utf-8") as f:
                    items = json.load(f)
                    for item in items:
                        self.url_queue.items.append(item)
                if self.url_queue.items:
                    self.url_queue.counter = max(item.get('id', 0) for item in self.url_queue.items)
                self.log(f"[SYSTEM] Restored {len(self.url_queue.items)} items from saved queue.")
            except Exception as e:
                self.log(f"[ERROR] Failed to restore saved queue: {e}")


    def update_count(self):
        with self.lock:
            self.total_in_queue = self.url_queue.qsize() + self.vault_queue.qsize()

    def log(self, message: str):
        with self.lock:
            self.logs.append(message)
            try:
                sys.stdout.write(f"\n[SYSTEM] {message}\n")
                sys.stdout.flush()
            except Exception:
                try:
                    clean_msg = message.encode('ascii', errors='replace').decode('ascii')
                    sys.stdout.write(f"\n[SYSTEM] {clean_msg}\n")
                    sys.stdout.flush()
                except Exception:
                    pass

    def set_status(self, status: str, url: str = None):
        with self.lock:
            self.current_status = status
            if url is not None:
                self.current_track_url = url

    def increment_completed(self):
        with self.lock:
            self.completed_count += 1

    def _update_vault_stats(self):
        vt = 0
        fb = collections.defaultdict(int)
        valid_folders = {"Classics", "Old School 70s80s", "Throwbacks 90s2000s", "New School 2010+", "Shows", "Live"}
        from config import CSV_BLUEPRINT
        import csv, os
        if os.path.exists(CSV_BLUEPRINT):
            try:
                with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        vt += 1
                        path = row.get('File Path', '')
                        if path:
                            parts = path.replace('\\', '/').split('/')
                            if len(parts) > 2 and parts[0].upper() == 'Z:':
                                folder = parts[1]
                                if folder in valid_folders:
                                    fb[folder] += 1
            except:
                pass
        self.vault_total = vt
        self.folder_breakdown = dict(fb)

    def get_snapshot(self):
        with self.lock:
            import time
            if time.time() - self.last_stat_update > 5:
                self.last_stat_update = time.time()
                self._update_vault_stats()
                
            return {
                "total_in_queue": self.total_in_queue,
                "completed_count": self.completed_count,
                "current_status": self.current_status,
                "current_track_url": self.current_track_url,
                "logs": list(self.logs),
                "vault_total": self.vault_total,
                "folder_breakdown": self.folder_breakdown,
                "download_queue": self.url_queue.get_all(),
                "pending_iheart_queue": list(self.pending_iheart_queue)
            }

    def start_spinner(self):
        def spin():
            spinner = itertools.cycle(['|', '/', '-', '\\'])
            while True:
                with self.lock:
                    status = self.current_status
                    last_log = self.logs[-1] if self.logs else 'Processing'
                
                if status == "Running":
                    display_log = last_log
                    if len(display_log) > 65:
                        display_log = display_log[:62] + "..."
                    try:
                        sys.stdout.write(f"\r[WORKING] {display_log} {next(spinner)}".ljust(90))
                        sys.stdout.flush()
                    except Exception:
                        try:
                            clean_log = display_log.encode('ascii', errors='replace').decode('ascii')
                            sys.stdout.write(f"\r[WORKING] {clean_log} {next(spinner)}".ljust(90))
                            sys.stdout.flush()
                        except Exception:
                            pass
                time.sleep(0.15)
        threading.Thread(target=spin, daemon=True).start()

state = SystemState()

def is_video_title(title: str) -> bool:
    t = title.lower()
    bad_phrases = ["official video", "music video", "official music video", "video clip", "videoclip", "lyric video", "lyrics video"]
    for phrase in bad_phrases:
        if phrase in t:
            return True
    if "(video)" in t or "[video]" in t:
        return True
    return False

def extract_playlist_urls(playlist_url: str) -> list:
    cmd = YT_DLP_CMD + ["--flat-playlist", "--print", "url", playlist_url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return [line.strip() for line in result.stdout.split('\n') if 'watch?v=' in line or 'youtu.be/' in line]
    except Exception as e:
        state.log(f"[ERROR] Playlist Extraction Failed: {e}")
        logging.error(f"Playlist extraction error for {playlist_url}: {e}")
        return []

def downloader_worker():
    while not state.stop_event.is_set():
        try:
            task = state.url_queue.get(timeout=1)
        except queue.Empty:
            if state.vault_queue.empty():
                state.set_status("Idle")
            continue

        url = task.get('url')
        task_type = task.get('type', 'ingest')
        target_override = task.get('target')
        
        if task_type == 'local':
            state.set_status("Running", os.path.basename(url))
            task_id = task.get('task_id')
            raw_path = url
            bitrate = "320"
            original_is_video = False
            meta = {'release_year': 'Unknown', 'lyrics': 'Not Found', 'url': 'Local Upload', 'item_type': 'Music'}
            staging_task_dir = os.path.dirname(raw_path)
            
            # Skip validation and download phases, go straight to Phase 2.5 (Auto-Mastering)
            am = AutoMaster()
            handoff_success = False
            
            try:
                state.log(f"Phase 2.5: Auto-Mastering Local Upload [{os.path.basename(raw_path)}]")
                mastered_path, updates = am.process_file(raw_path, original_bitrate=bitrate)
                
                if not mastered_path:
                    handoff_success = False
                    continue
                
                meta.update(updates)
                
                # Era-to-Energy Category Folder Interception
                release_year = meta.get('release_year', 'Unknown')
                clean_name = os.path.basename(mastered_path)
                
                track_title = meta.get('title', 'Unknown Title')
                if is_video_title(track_title) or is_video_title(clean_name):
                    state.log(f"[WARNING] Video keyword detected in title or original filename: \"{track_title}\" - routing to Unsorted_Review for manual inspection.")
                    era_folder = "Unsorted_Review"
                    target_override = "Unsorted_Review"
                elif target_override:
                    era_folder = target_override
                else:
                    if "live" in clean_name.lower():
                        era_folder = "Live"
                    elif not release_year or str(release_year).lower() in ['unknown', 'verify year', '']:
                        era_folder = "Unsorted_Review"
                    else:
                        try:
                            year_int = int(str(release_year)[:4])
                            if year_int < 1970:
                                era_folder = "Classics"
                            elif 1970 <= year_int <= 1989:
                                era_folder = "Old School 70s80s"
                            elif 1990 <= year_int <= 2009:
                                era_folder = "Throwbacks 90s2000s"
                            else:
                                era_folder = "New School 2010+"
                        except:
                            era_folder = "Unsorted_Review"
                    target_override = era_folder
                
                # Map the era_folder to a clean era name for energy_category
                clean_cat = "Throwbacks"
                folder_lower = era_folder.lower()
                if "classics" in folder_lower:
                    clean_cat = "Classics"
                elif "old school" in folder_lower:
                    clean_cat = "Old School"
                elif "throwbacks" in folder_lower:
                    clean_cat = "Throwbacks"
                elif "new school" in folder_lower:
                    clean_cat = "New School"
                
                meta['energy_category'] = clean_cat

                state.vault_queue.put({'path': mastered_path, 'meta': meta, 'task_id': task_id, 'target': target_override})
                handoff_success = True
                
            except Exception as e:
                state.log(f"[CRITICAL LOCAL UPLOADER ERROR] {e}")
                logging.exception(f"Local Uploader Worker CRASH for {url}: {e}")
            finally:
                if not handoff_success:
                    if os.path.exists(staging_task_dir):
                        shutil.rmtree(staging_task_dir, ignore_errors=True)
                
                state.url_queue.task_done()
                state.update_count()
            continue

        # YTM-DLP: Custom search interceptor for raw text queries
        if url and not url.startswith("http://") and not url.startswith("https://"):
            query_str = url
            if query_str.startswith("ytsearch1:"):
                query_str = query_str[len("ytsearch1:"):]
            elif query_str.startswith("ytsearch:"):
                query_str = query_str[len("ytsearch:"):]
            
            import re
            query_clean = re.sub(r'\[.*?\]', '', query_str).strip()
            query_clean = re.sub(r'\s+', ' ', query_clean)
            
            state.log(f"[YTM-DLP] Resolving query: \"{query_str}\" (Cleaned: \"{query_clean}\") via YouTube Music API...")
            try:
                from ytmusicapi import YTMusic
                ytm = YTMusic()
                results = ytm.search(query_clean, filter="songs")
                if results and isinstance(results, list):
                    # Iterate to find the first result that is NOT a video title
                    best_match = None
                    for res in results:
                        title_candidate = res.get('title', '')
                        if not is_video_title(title_candidate):
                            best_match = res
                            break
                    # Fallback to the top result if all contain video keywords
                    if not best_match:
                        best_match = results[0]
                        
                    video_id = best_match.get('videoId')
                    if video_id:
                        resolved_url = f"https://music.youtube.com/watch?v={video_id}"
                        title = best_match.get('title', 'Unknown')
                        artists = ", ".join([a.get('name', 'Unknown') for a in best_match.get('artists', [])])
                        duration = best_match.get('duration', '--:--')
                        state.log(f"[YTM-DLP] Resolved to: \"{title}\" by {artists} ({duration}) -> {resolved_url}")
                        url = resolved_url
                    else:
                        state.log(f"[WARNING] No videoId found in ytmusicapi results for \"{query_clean}\". Falling back to ytsearch1.")
                        url = f"ytsearch1:{query_clean}"
                else:
                    state.log(f"[WARNING] No results from ytmusicapi for \"{query_clean}\". Falling back to ytsearch1.")
                    url = f"ytsearch1:{query_clean}"
            except Exception as e:
                state.log(f"[ERROR] ytmusicapi search failed: {e}. Falling back to ytsearch1.")
                url = f"ytsearch1:{query_clean}"
        
        state.set_status("Running", url)
        task_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        staging_task_dir = os.path.join(STAGING_DIR, task_id)
        
        gk, tr, am = Gatekeeper(), Transporter(), AutoMaster()
        handoff_success = False

        try:
            state.log(f"Phase 1: Validating {url}")
            v, meta = gk.process_request(url)
            
            # THE GATEKEEPER BLIND PASS-THROUGH OR VETO
            if not v:
                if meta.get("veto"):
                    state.log(f"[VETO] Rejection: {meta.get('error', 'Strict veto applied.')}")
                    handoff_success = False
                    continue
                state.log(f"[WARNING] Gatekeeper blind: {meta.get('error', 'Unknown')}. Forcing SomeDL override.")
                meta = {'release_year': 'Unknown', 'lyrics': 'Not Found', 'url': url}

            state.log(f"Phase 2: Downloading via SomeDL")
            raw_path, bitrate = tr.download_track(url, task_id=task_id)
            
            original_is_video = False
            if raw_path:
                original_is_video = is_video_title(os.path.basename(raw_path))
            
            # --- THE GRACEFUL SKIP & DEAD LINK LOGGER ---
            # If the download failed entirely or generated a dummy file
            if not raw_path or "blind_pass_through" in raw_path:
                state.log(f"[SKIP] Track completely blocked. Logging dead link.")
                dead_link_log = os.path.join(LOG_DIR, "failed_downloads.txt")
                with open(dead_link_log, "a", encoding="utf-8") as f:
                    f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {url}\n")
                
                handoff_success = False
                continue
            # --------------------------------------------

            state.log(f"Phase 2.5: Auto-Mastering (Fingerprinting)")
            mastered_path, updates = am.process_file(raw_path, original_bitrate=bitrate)
            
            if not mastered_path:
                handoff_success = False
                continue
            
            # Protect the yt-dlp year
            if updates.get('release_year') in ["Unknown", "Verify Year", ""]:
                gk_year = meta.get('release_year')
                if gk_year and gk_year not in ["Unknown", "Verify Year", ""]:
                    updates['release_year'] = gk_year
            
            meta.update(updates)
            
            # Era-to-Energy Category Folder Interception
            release_year = meta.get('release_year', 'Unknown')
            clean_name = os.path.basename(mastered_path)
            
            track_title = meta.get('title', 'Unknown Title')
            if original_is_video or is_video_title(track_title) or is_video_title(clean_name):
                state.log(f"[WARNING] Video keyword detected in title or original filename: \"{track_title}\" - routing to Unsorted_Review for manual inspection.")
                era_folder = "Unsorted_Review"
                target_override = "Unsorted_Review"
            elif target_override:
                era_folder = target_override
            else:
                if "live" in clean_name.lower():
                    era_folder = "Live"
                elif not release_year or str(release_year).lower() in ['unknown', 'verify year', '']:
                    era_folder = "Unsorted_Review"
                else:
                    try:
                        year_int = int(str(release_year)[:4])
                        if year_int < 1970:
                            era_folder = "Classics"
                        elif 1970 <= year_int <= 1989:
                            era_folder = "Old School 70s80s"
                        elif 1990 <= year_int <= 2009:
                            era_folder = "Throwbacks 90s2000s"
                        else:
                            era_folder = "New School 2010+"
                    except:
                        era_folder = "Unsorted_Review"
                target_override = era_folder
            
            # Map the era_folder to a clean era name for energy_category
            clean_cat = "Throwbacks"
            folder_lower = era_folder.lower()
            if "classics" in folder_lower:
                clean_cat = "Classics"
            elif "old school" in folder_lower:
                clean_cat = "Old School"
            elif "throwbacks" in folder_lower:
                clean_cat = "Throwbacks"
            elif "new school" in folder_lower:
                clean_cat = "New School"
            
            meta['energy_category'] = clean_cat

            # Pass explicit status from queue item
            if 'explicit' in task:
                meta['explicit'] = task['explicit']
            else:
                # Fallback: check track title for keywords
                title_lower = track_title.lower()
                if 'explicit' in title_lower:
                    meta['explicit'] = True
                elif 'clean' in title_lower:
                    meta['explicit'] = False

            state.vault_queue.put({'path': mastered_path, 'meta': meta, 'task_id': task_id, 'target': target_override})
            handoff_success = True
            
        except Exception as e:
            state.log(f"[CRITICAL DOWNLOADER ERROR] {e}")
            logging.exception(f"Downloader Worker CRASH for {url}: {e}")
        finally:
            if not handoff_success:
                if os.path.exists(staging_task_dir):
                    shutil.rmtree(staging_task_dir, ignore_errors=True)
            
            state.url_queue.task_done()
            state.update_count()

def vault_worker():
    while not state.stop_event.is_set():
        try:
            task = state.vault_queue.get(timeout=1)
        except queue.Empty:
            continue
        
        vm = VaultManager()
        clean_name = os.path.basename(task['path'])
        dest = task.get('target') or "Eras"
        state.log(f"Phase 3: Vaulting [{clean_name}] to -> {dest}")
        
        status, message = vm.store_track(task['path'], task['meta'], task['task_id'], task.get('target'))
        
        if status:
            state.log(f"Complete: {clean_name}")
            state.increment_completed()
        elif "Duplicate" in message:
            state.log(f"[SKIPPED] Duplicate Track: {clean_name}")
        else:
            state.log(f"[VAULT ERROR] {clean_name}: {message}")
            logging.error(f"Vaulting failure for {clean_name}: {message}")
        state.vault_queue.task_done()
        state.update_count()

def iheart_poller_worker():
    import urllib.request
    import json
    import urllib.error
    import csv
    import re
    from datetime import datetime
    from config import (
        IHEART_STATION_ID, IHEART_POLL_INTERVAL, IHEART_CHURCH_FOLDER,
        IHEART_CHURCH_DAYS, IHEART_CHURCH_START_HOUR, IHEART_CHURCH_END_HOUR,
        IHEART_CHURCH_KEYWORDS
    )
    
    state.log("[iHeart Sync] Listening for missing tracks")
    
    last_track_id = None
    last_track_key = None
    
    api_url = f"https://us.api.iheart.com/api/v3/live-meta/stream/{IHEART_STATION_ID}/currentTrackMeta?defaultMetadata=true"
    
    while not state.stop_event.is_set():
        try:
            req = urllib.request.Request(
                api_url, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    track_id = data.get('trackId')
                    artist = data.get('artist', '').strip()
                    title = data.get('title', '').strip()
                    album = data.get('album', '').strip()
                    
                    # Create a unique key for the currently playing track
                    current_key = f"{artist} - {title}".lower().strip()
                    
                    if current_key and (track_id != last_track_id or current_key != last_track_key):
                        last_track_id = track_id
                        last_track_key = current_key
                        
                        if not artist or not title or artist.lower() in ["unknown artist", ""] or title.lower() in ["unknown title", ""]:
                            continue
                            
                        logging.info(f"[iHeart Sync] Now Playing on WVAZ V103: \"{title}\" by {artist}")
                        
                        # 1. Determine Target Routing (Sunday Church or Era-based)
                        now = datetime.now()
                        is_sunday_church = False
                        
                        if now.weekday() in IHEART_CHURCH_DAYS:
                            if IHEART_CHURCH_START_HOUR <= now.hour < IHEART_CHURCH_END_HOUR:
                                is_sunday_church = True
                                
                        if not is_sunday_church:
                            search_string = f"{artist} {title} {album}".lower()
                            for kw in IHEART_CHURCH_KEYWORDS:
                                if kw in search_string:
                                    is_sunday_church = True
                                    logging.info(f"[iHeart Sync] Keyword matched ('{kw}') -> Routing to Gospel/Church.")
                                    break
                                    
                        if is_sunday_church:
                            target_override = IHEART_CHURCH_FOLDER
                            logging.info(f"[iHeart Sync] Routing \"{title}\" to Church folder -> {IHEART_CHURCH_FOLDER}")
                        else:
                            target_override = ""
                            
                        # 2. Check for duplicates in library using smart matching
                        from modules.storage import VaultManager
                        vm = VaultManager()
                        
                        def is_smart_duplicate(existing_name, check_artist, check_title):
                            # First try direct normalized key match (fast path)
                            norm_key = vm._normalize_track_key(f"{check_artist} - {check_title}")
                            if vm._normalize_track_key(existing_name) == norm_key:
                                return True, "Already in library (normalized match)"
                                
                            # Fallback to smart parsing
                            parts = existing_name.split(' - ', 1)
                            if len(parts) == 2:
                                ex_artist, ex_title = parts
                            else:
                                ex_artist, ex_title = "", existing_name
                                
                            def norm_title(t):
                                t = re.sub(r'\[.*?\]', '', t)
                                t = re.sub(r'\((?:feat\.?|featuring|f/)\.?\s+.*?\)', '', t, flags=re.I)
                                return re.sub(r'[^a-z0-9]', '', t.lower())
                                
                            if norm_title(ex_title) != norm_title(check_title):
                                return False, ""
                                
                            # Titles match! Now extract and clean co-artists
                            def get_primary_artists(a):
                                a_clean = a.lower()
                                a_clean = re.split(r'\s+(?:feat\.?|featuring|with|w/|f/|and|&)\s+', a_clean)[0]
                                parts = re.split(r'[,/;]', a_clean)
                                return [re.sub(r'[^a-z0-9]', '', x).strip() for x in parts if re.sub(r'[^a-z0-9]', '', x).strip()]
                                
                            ex_artists = get_primary_artists(ex_artist)
                            check_artists = get_primary_artists(check_artist)
                            
                            if set(ex_artists) & set(check_artists):
                                return True, "Already in library (smart artist match)"
                                
                            ex_artist_clean = re.sub(r'[^a-z0-9]', '', ex_artist.lower())
                            check_artist_clean = re.sub(r'[^a-z0-9]', '', check_artist.lower())
                            if ex_artist_clean in check_artist_clean or check_artist_clean in ex_artist_clean:
                                return True, "Already in library (substring artist match)"
                                
                            return False, ""

                        is_duplicate = False
                        duplicate_reason = ""
                        
                        from config import CSV_BLUEPRINT
                        if os.path.exists(CSV_BLUEPRINT):
                            with vm._csv_lock:
                                try:
                                    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                                        reader = csv.DictReader(f)
                                        for row in reader:
                                            existing_name = row.get('Track Name')
                                            if existing_name:
                                                is_dup, reason = is_smart_duplicate(existing_name, artist, title)
                                                if is_dup:
                                                    is_duplicate = True
                                                    duplicate_reason = reason
                                                    break
                                except Exception as e:
                                    logging.error(f"Error checking CSV in iHeart poller: {e}")
                                    
                        if not is_duplicate:
                            g_drive_base = r"G:\My Drive\FMP MUSIC\BASE\MUSIC"
                            if os.path.exists(g_drive_base):
                                for folder in vm.era_folders:
                                    folder_path = os.path.join(g_drive_base, folder)
                                    if os.path.exists(folder_path):
                                        for f in os.listdir(folder_path):
                                            if f.lower().endswith(".mp3"):
                                                f_name_without_ext = f[:-4]
                                                is_dup, reason = is_smart_duplicate(f_name_without_ext, artist, title)
                                                if is_dup:
                                                    is_duplicate = True
                                                    duplicate_reason = f"Already exists on G Drive: {folder}/{f} ({reason})"
                                                    break
                                    if is_duplicate:
                                        break
                                        
                        if is_duplicate:
                            logging.info(f"[iHeart Sync] Skipped (Duplicate): \"{title}\" by {artist} ({duplicate_reason})")
                        else:
                            query_clean = re.sub(r'\[.*?\]', '', f"{artist} - {title}").strip()
                            query_clean = re.sub(r'\s+', ' ', query_clean)
                            
                            # Check if already in pending_iheart_queue, download_queue, or rejected
                            already_pending = False
                            with state.lock:
                                for item in state.pending_iheart_queue:
                                    if item.get('url', '').lower() == query_clean.lower():
                                        already_pending = True
                                        break
                                for item in state.url_queue.get_all():
                                    if item.get('url', '').lower() == query_clean.lower():
                                        already_pending = True
                                        break
                                for rej_url in state.rejected_iheart:
                                    if rej_url.lower() == query_clean.lower():
                                        already_pending = True
                                        break
                                        
                            if already_pending:
                                continue
                                
                            state.log(f"[iHeart Sync] Missing Track! Staging in Discoveries: \"{query_clean}\"")
                            with state.lock:
                                state.pending_iheart_queue.append({
                                    'url': query_clean,
                                    'target': target_override,
                                    'artist': artist,
                                    'title': title,
                                    'timestamp': datetime.now().strftime("%I:%M %p")
                                })
                                state.save_pending()
                            state.update_count()
                            
        except urllib.error.URLError as e:
            logging.debug(f"iHeart API Poll network error: {e}")
        except Exception as e:
            logging.error(f"Unexpected error in iheart_poller_worker: {e}")
            
        time.sleep(IHEART_POLL_INTERVAL)

def start_engines():
    state.stop_event.clear()
    
    if not state.boot_cleared:
        state.log("System Hard Reset: Purging Staging...")
        if os.path.exists(STAGING_DIR):
            shutil.rmtree(STAGING_DIR, ignore_errors=True)
        os.makedirs(STAGING_DIR, exist_ok=True)
        
        if os.path.exists(CSV_BLUEPRINT):
            backup_path = os.path.join(BACKUP_DIR, "fmp_data_7718_backup.csv")
            try:
                shutil.copy2(CSV_BLUEPRINT, backup_path)
                state.log("Database backup verified.")
            except Exception as e:
                state.log(f"!! Backup Failed: {e}")
                logging.error(f"CSV Backup routine failed: {e}")
        
        state.boot_cleared = True

    if not state.downloader_thread or not state.downloader_thread.is_alive():
        state.downloader_thread = threading.Thread(target=downloader_worker, daemon=True)
        state.downloader_thread.start()
    if not state.vault_thread or not state.vault_thread.is_alive():
        state.vault_thread = threading.Thread(target=vault_worker, daemon=True)
        state.vault_thread.start()
        
    # Start the iHeart Sync engine if enabled
    from config import IHEART_SYNC_ENABLED
    if IHEART_SYNC_ENABLED:
        if not hasattr(state, 'iheart_thread') or not state.iheart_thread or not state.iheart_thread.is_alive():
            state.iheart_thread = threading.Thread(target=iheart_poller_worker, daemon=True)
            state.iheart_thread.start()

# --- WEB ROUTES ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/shows')
def get_shows():
    try:
        rclone_path = os.path.join(BASE_DIR, "rclone.exe")
        cmd = [rclone_path, "lsf", "citrus3:/Shows/", "--dirs-only"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        shows = [line.strip().rstrip('/') for line in result.stdout.split('\n') if line.strip()]
        return jsonify(shows)
    except Exception as e:
        state.log(f"[ERROR] Could not fetch shows via Rclone: {e}")
        logging.error(f"Rclone Shows fetch error: {e}")
        return jsonify([])

@app.route('/api/new_show', methods=['POST'])
def new_show():
    show_name = request.json.get('show_name')
    if show_name:
        safe_name = "".join(c for c in show_name if c.isalnum() or c in (' ', '_', '-')).strip()
        try:
            rclone_path = os.path.join(BASE_DIR, "rclone.exe")
            cmd = [rclone_path, "mkdir", f"citrus3:/Shows/{safe_name}"]
            subprocess.run(cmd, check=True)
        except Exception as e:
            state.log(f"[ERROR] Failed to create remote show folder via rclone: {e}")
            logging.error(f"Remote folder creation failed for {safe_name}: {e}")
    return jsonify({"status": "ok"})

def replace_show_in_path(path_val, old_name, new_name):
    if not path_val:
        return path_val
    norm_path = path_val.replace('\\', '/')
    parts = norm_path.split('/')
    try:
        shows_idx = parts.index('Shows')
        if shows_idx < len(parts) - 1 and parts[shows_idx + 1] == old_name:
            parts[shows_idx + 1] = new_name
            sep = '\\' if '\\' in path_val else '/'
            return sep.join(parts)
    except ValueError:
        pass
    return path_val

def background_rename_task(old_name, new_name):
    import csv
    try:
        # 1. Run Rclone moveto
        rclone_path = os.path.join(BASE_DIR, "rclone.exe")
        state.log(f"[RENAME] Starting remote Citrus3 FTP folder move: Shows/{old_name} -> Shows/{new_name}")
        cmd = [rclone_path, "moveto", f"citrus3:/Shows/{old_name}", f"citrus3:/Shows/{new_name}"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            state.log(f"[WARNING] Remote move returned non-zero code. Folder may not exist on FTP yet. Output: {res.stderr.strip()}")
        else:
            state.log(f"[RENAME] Remote Citrus3 FTP folder move completed successfully.")

        # 2. Backup configs/fmp_data_7718.csv
        state.log(f"[RENAME] Creating database backup before rewrite...")
        backup_filename = f"fmp_data_7718_rename_{old_name}_to_{new_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        backup_path = os.path.join(BACKUP_DIR, backup_filename)
        
        vm = VaultManager()
        updated_count = 0
        with vm._csv_lock:
            if os.path.exists(CSV_BLUEPRINT):
                shutil.copy2(CSV_BLUEPRINT, backup_path)
                
                # 3. Rewrite matching rows
                rows = []
                fieldnames = []
                with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    fieldnames = reader.fieldnames
                    for row in reader:
                        path_val = row.get('File Path')
                        if path_val:
                            new_path_val = replace_show_in_path(path_val, old_name, new_name)
                            if new_path_val != path_val:
                                row['File Path'] = new_path_val
                                updated_count += 1
                        rows.append(row)
                
                if updated_count > 0:
                    with open(CSV_BLUEPRINT, 'w', encoding='utf-8', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)
                    state.log(f"[RENAME] Database updated: Modified path for {updated_count} tracks.")
                else:
                    state.log(f"[RENAME] No matching tracks found in database to update.")
            else:
                state.log(f"[ERROR] CSV database file not found at {CSV_BLUEPRINT}")
                return

        # 4. Trigger Git Auto Push
        from config import AUTO_GIT_PUSH
        if AUTO_GIT_PUSH and updated_count > 0:
            state.log(f"[RENAME] Synchronizing updated database to GitHub...")
            with vm._git_lock:
                try:
                    subprocess.run(["git", "add", "configs/fmp_data_7718.csv"], check=True, capture_output=True)
                    commit_msg = f"Rename show {old_name} to {new_name}"
                    subprocess.run(["git", "commit", "-m", commit_msg], check=True, capture_output=True)
                    subprocess.run(["git", "push", "origin", "main"], check=True, capture_output=True)
                    state.log(f"[RENAME] GitHub database synchronization successful.")
                except Exception as git_err:
                    state.log(f"[ERROR] GitHub synchronization failed: {git_err}")

    except Exception as e:
        state.log(f"[ERROR] Background rename task failed: {e}")
        logging.exception(f"Background rename crash: {e}")

@app.route('/api/rename_show', methods=['POST'])
def rename_show():
    data = request.json or {}
    old_name = data.get('old_name', '').strip()
    new_name = data.get('new_name', '').strip()
    
    if not old_name or not new_name:
        return jsonify({"status": "error", "message": "Show names cannot be empty"}), 400
        
    safe_old = "".join(c for c in old_name if c.isalnum() or c in (' ', '_', '-')).strip()
    safe_new = "".join(c for c in new_name if c.isalnum() or c in (' ', '_', '-')).strip()
    
    if not safe_old or not safe_new:
        return jsonify({"status": "error", "message": "Invalid show names"}), 400

    if safe_old == safe_new:
        return jsonify({"status": "error", "message": "Old name and new name are identical"}), 400

    # Local G: drive folder rename
    g_drive_base = r"G:\My Drive\FMP MUSIC\BASE\MUSIC"
    old_local_dir = os.path.join(g_drive_base, "Shows", safe_old)
    new_local_dir = os.path.join(g_drive_base, "Shows", safe_new)
    
    if os.path.exists(g_drive_base):
        if os.path.exists(old_local_dir):
            try:
                os.makedirs(os.path.dirname(new_local_dir), exist_ok=True)
                os.rename(old_local_dir, new_local_dir)
                state.log(f"[RENAME] Local directory renamed: Shows/{safe_old} -> Shows/{safe_new}")
            except Exception as e:
                state.log(f"[ERROR] Failed to rename local folder: {e}")
                return jsonify({"status": "error", "message": f"Local rename failed: {str(e)}"}), 500
        else:
            state.log(f"[RENAME] Local folder Shows/{safe_old} not found. Skipping local rename.")
    else:
        state.log(f"[WARNING] G: drive base path not found. Skipping local rename.")

    # Start background thread to rename remote and update CSV + Git
    threading.Thread(
        target=background_rename_task,
        args=(safe_old, safe_new),
        daemon=True
    ).start()
    
    return jsonify({
        "status": "ok",
        "message": f"Rename from '{safe_old}' to '{safe_new}' initiated successfully."
    })

def resolve_yt_meta_bg(item_id, url):
    try:
        cmd = ["yt-dlp", "--skip-download", "--dump-json", "--no-warnings", url]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        import json
        data = json.loads(result.stdout.strip().split('\n')[0])
        title = data.get('title')
        artist = data.get('artist') or data.get('creator') or data.get('uploader')
        
        # Check title for explicit/clean keywords
        explicit_status = None
        title_lower = (title or '').lower()
        if 'explicit' in title_lower:
            explicit_status = True
        elif 'clean' in title_lower:
            explicit_status = False

        with state.url_queue.lock:
            for item in state.url_queue.items:
                if item.get('id') == item_id:
                    item['title'] = title
                    item['artist'] = artist
                    item['is_yt'] = True
                    if explicit_status is not None:
                        item['explicit'] = explicit_status
                    break
            state.url_queue._save_to_disk()
    except Exception as e:
        logging.error(f"Failed to resolve meta for queue item {item_id}: {e}")

def get_playlist_explicit_statuses_and_other_versions(playlist_url: str):
    """
    Downloads the playlist page, extracts:
    1. A list of booleans representing track explicit status by index.
    2. The other version playlist ID and title if present in the 'Other versions' shelf.
    """
    import urllib.request
    import re
    import json
    import codecs

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    req = urllib.request.Request(playlist_url, headers=headers)
    try:
        html = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='ignore')
        
        # Parse initialData
        scripts = re.findall(r'<script.*?>.*?</script>', html, re.DOTALL)
        data_script = None
        for script in scripts:
            if 'initialData' in script and ('MUSIC_EXPLICIT_BADGE' in script or 'musicResponsiveListItemRenderer' in script):
                data_script = script
                break
                
        if not data_script:
            return [], None, None
            
        matches = re.findall(r"data:\s*'([\s\S]*?)'", data_script)
        if len(matches) < 2:
            return [], None, None
            
        decoded = matches[1].encode('utf-8').decode('unicode_escape')
        data = json.loads(decoded)
        
        def find_nodes(node, key):
            results = []
            if isinstance(node, dict):
                if key in node:
                    results.append(node[key])
                for k, v in node.items():
                    results.extend(find_nodes(v, key))
            elif isinstance(node, list):
                for item in node:
                    results.extend(find_nodes(item, key))
            return results

        # Extract explicit statuses
        items = find_nodes(data, 'musicResponsiveListItemRenderer')
        statuses = []
        for item in items:
            is_explicit = False
            badges = item.get('badges', [])
            for badge in badges:
                try:
                    badge_type = badge['musicInlineBadgeRenderer']['icon']['iconType']
                    if badge_type == 'MUSIC_EXPLICIT_BADGE':
                        is_explicit = True
                except:
                    pass
            statuses.append(is_explicit)
            
        # Find other versions
        other_version_id = None
        other_version_title = None
        
        current_album_title = None
        try:
            current_album_title = data['header']['musicDetailHeaderRenderer']['title']['runs'][0]['text']
        except:
            pass
            
        if not current_album_title:
            try:
                current_album_title = data.get('title', '')
            except:
                pass
                
        shelves = find_nodes(data, 'musicCarouselShelfRenderer')
        for shelf in shelves:
            try:
                shelf_title = shelf['header']['musicCarouselShelfBasicHeaderRenderer']['title']['runs'][0]['text']
                if 'Other versions' in shelf_title:
                    for version_item in shelf.get('contents', []):
                        renderer = version_item.get('musicTwoRowItemRenderer', {})
                        version_title = "Unknown"
                        try:
                            version_title = renderer['title']['runs'][0]['text']
                        except:
                            pass
                            
                        if not current_album_title or version_title.strip().lower() == current_album_title.strip().lower():
                            playlist_ids = find_nodes(renderer, 'playlistId')
                            if playlist_ids:
                                other_version_id = playlist_ids[0]
                                other_version_title = version_title
                                break
                    if other_version_id:
                        break
            except:
                pass
                
        return statuses, other_version_id, other_version_title
    except Exception as e:
        logging.error(f"Error parsing playlist details from URL {playlist_url}: {e}")
        return [], None, None

def process_playlist_addition(u, target, auto_linked):
    """Parses playlist details, enqueues tracks with explicit status, and auto-links counterpart album."""
    explicit_statuses, counterpart_id, counterpart_title = get_playlist_explicit_statuses_and_other_versions(u)
    track_urls = extract_playlist_urls(u)
    
    for idx, track_url in enumerate(track_urls):
        is_explicit = False
        if idx < len(explicit_statuses):
            is_explicit = explicit_statuses[idx]
            
        item_id = state.url_queue.put({
            'url': track_url, 
            'type': 'ingest', 
            'target': target,
            'explicit': is_explicit
        })
        threading.Thread(target=resolve_yt_meta_bg, args=(item_id, track_url), daemon=True).start()
        
    if counterpart_id and not auto_linked:
        counterpart_url = f"https://music.youtube.com/playlist?list={counterpart_id}"
        state.log(f"[Auto-Link] Found counterpart album: \"{counterpart_title}\" ({counterpart_id}) -> Auto-queuing counterpart...")
        def enqueue_counterpart():
            process_playlist_addition(counterpart_url, target, auto_linked=True)
            state.update_count()
        threading.Thread(target=enqueue_counterpart, daemon=True).start()

@app.route('/add', methods=['POST'])
def add():
    raw_urls = request.json.get('urls', '').split('\n')
    target = request.json.get('target', '')
    title = request.json.get('title')
    artist = request.json.get('artist')
    auto_linked = request.json.get('auto_linked', False)
    
    for u in raw_urls:
        u = u.strip()
        if not u: continue
        
        item_data = {'url': u, 'type': 'ingest', 'target': target}
        if len(raw_urls) == 1 and title and artist:
            item_data['title'] = title
            item_data['artist'] = artist
            item_data['is_yt'] = True
            
        if "list=" in u or "playlist" in u.lower():
            process_playlist_addition(u, target, auto_linked)
        elif not u.startswith("http://") and not u.startswith("https://") and not u.startswith("ytsearch"):
            state.url_queue.put({'url': f"ytsearch1:{u}", 'type': 'ingest', 'target': target})
        else: 
            item_id = state.url_queue.put(item_data)
            if 'title' not in item_data:
                threading.Thread(target=resolve_yt_meta_bg, args=(item_id, u), daemon=True).start()
                
    state.update_count()
    start_engines()
    return jsonify({"status": "ok"})

@app.route('/upload_local', methods=['POST'])
def upload_local():
    target = request.form.get('target', '')
    if 'file' not in request.files: return jsonify({"status": "error"})
    
    file = request.files['file']
    if file.filename == '': return jsonify({"status": "error"})
    
    task_id = datetime.now().strftime("%Y%m%d_%H%M%S_local")
    staging_path = os.path.join(STAGING_DIR, task_id)
    os.makedirs(staging_path, exist_ok=True)
    
    raw_path = os.path.join(staging_path, file.filename)
    file.save(raw_path)
    
    # Enqueue in state.url_queue for thread-safe sequential processing
    state.url_queue.put({
        'url': raw_path,
        'type': 'local',
        'task_id': task_id,
        'target': target
    })
    
    state.update_count()
    start_engines()
    return jsonify({"status": "ok"})

@app.route('/stop', methods=['POST'])
def stop(): 
    state.stop_event.set()
    state.set_status("Stopping...")
    return jsonify({"status": "ok"})

@app.route('/api/queue/remove', methods=['POST'])
def queue_remove():
    item_id = request.json.get('id')
    if item_id is not None:
        removed = state.url_queue.remove(int(item_id))
        state.update_count()
        if removed:
            state.log(f"[QUEUE] Removed item ID {item_id} from active download queue.")
            return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "Item not found in queue"})

@app.route('/api/pending/approve_all', methods=['POST'])
def pending_approve_all():
    approved_count = 0
    with state.lock:
        for item in state.pending_iheart_queue:
            url = item.get('url')
            target = item.get('target', '')
            state.url_queue.put({'url': url, 'type': 'ingest', 'target': target})
            approved_count += 1
        state.pending_iheart_queue.clear()
        state.save_pending()
    if approved_count > 0:
        state.update_count()
        start_engines()
        state.log(f"[iHeart Sync] Approved all {approved_count} discoveries -> Sent to download queue.")
        return jsonify({"status": "ok", "message": f"Successfully enqueued {approved_count} tracks."})
    return jsonify({"status": "ok", "message": "No discoveries to approve."})

@app.route('/api/pending/approve', methods=['POST'])
def pending_approve():
    url = request.json.get('url')
    target = request.json.get('target', '')
    found = False
    with state.lock:
        for i, item in enumerate(state.pending_iheart_queue):
            if item['url'] == url:
                state.pending_iheart_queue.pop(i)
                found = True
                state.save_pending()
                break
    if found:
        state.url_queue.put({'url': url, 'type': 'ingest', 'target': target})
        state.update_count()
        start_engines()
        state.log(f"[iHeart Sync] Approved track: {url} -> Sent to download queue.")
        return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "Track not found in pending list"})

@app.route('/api/pending/reject', methods=['POST'])
def pending_reject():
    url = request.json.get('url')
    found = False
    with state.lock:
        for i, item in enumerate(state.pending_iheart_queue):
            if item['url'] == url:
                state.pending_iheart_queue.pop(i)
                found = True
                if url not in state.rejected_iheart:
                    state.rejected_iheart.append(url)
                    state.save_rejected()
                state.save_pending()
                break
    if found:
        state.log(f"[iHeart Sync] Rejected track: {url}")
        return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "Track not found in pending list"})

@app.route('/api/pending/clear', methods=['POST'])
def pending_clear():
    with state.lock:
        count = len(state.pending_iheart_queue)
        state.pending_iheart_queue.clear()
        state.save_pending()
    state.log(f"[iHeart Sync] Cleared all {count} discoveries.")
    return jsonify({"status": "ok"})

@app.route('/api/search_yt', methods=['POST'])
def search_yt():
    query = request.json.get('query', '')
    if not query:
        return jsonify([])
    try:
        # If the user pasted a direct link, just dump its metadata.
        if query.startswith("http://") or query.startswith("https://"):
            cmd = ["yt-dlp", query, "--dump-json", "--no-warnings"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            results = []
            for line in result.stdout.strip().split('\n'):
                if not line: continue
                try:
                    import json
                    data = json.loads(line)
                    results.append({
                        'title': data.get('title', 'Unknown Title'),
                        'uploader': data.get('uploader', 'Unknown Artist'),
                        'duration_string': data.get('duration_string', '--:--'),
                        'url': data.get('webpage_url', '')
                    })
                except:
                    pass
            return jsonify(results)
        else:
            # Use ytmusicapi search for fast, official song results
            import re
            query_clean = re.sub(r'\[.*?\]', '', query).strip()
            query_clean = re.sub(r'\s+', ' ', query_clean)
            
            from ytmusicapi import YTMusic
            ytm = YTMusic()
            songs = ytm.search(query_clean, filter="songs")
            results = []
            for song in songs[:5]:
                video_id = song.get('videoId')
                if not video_id:
                    continue
                artists = ", ".join([a.get('name', 'Unknown') for a in song.get('artists', [])])
                results.append({
                    'title': song.get('title', 'Unknown Title'),
                    'uploader': artists,
                    'duration_string': song.get('duration', '--:--'),
                    'url': f"https://music.youtube.com/watch?v={video_id}"
                })
            return jsonify(results)
    except subprocess.CalledProcessError as e:
        state.log(f"[SEARCH ERROR] yt-dlp failed: {e.stderr}")
        logging.error(f"Search failed for {query}. Stderr: {e.stderr}")
        return jsonify([])
    except Exception as e:
        state.log(f"[SEARCH ERROR] {e}")
        logging.error(f"Search exception for {query}: {e}")
        return jsonify([])

@app.route('/search_scrub', methods=['POST'])
def search(): 
    try:
        return jsonify(VaultManager().find_candidates(request.json.get('query', '')))
    except Exception as e:
        print(f"\n[CRITICAL SEARCH ERROR] >>> {e} <<<\n")
        logging.error(f"Dashboard search crash: {e}")
        return jsonify([])

@app.route('/execute_scrub', methods=['POST'])
def execute():
    name = request.json.get('track_name', '')
    s, m = VaultManager().scrub_track(name)
    state.log(f"[ERASED] {name}" if s else f"[FAILED] {m}")
    if not s: logging.error(f"Scrub failed for {name}: {m}")
    return jsonify({"status": "ok"})

@app.route('/api/status')
def status():
    return jsonify(state.get_snapshot())

if __name__ == '__main__':
    os.system('') 
    
    red_banner = "\033[91m" + r"""================================================================================
                                 ChiTownSounds'
--------------------------------------------------------------------------------
   ______ __  __  _____     _    _  _  _    _                    _       
  |  ____|  \/  ||  __ \   | |  | || || |  (_)                  | |      
  | |__  | \  / || |__) |  | |  | || || |_  _  _ __ ___   __ _  | |_  ___
  |  __| | |\/| ||  ___/   | |  | || || __|| || '_ ` _ \ / _` | | __|/ _ \
  | |    | |  | || |       | |__| || || |_ | || | | | | | (_| | | |_|  __/
  |_|    |_|  |_||_|        \____/ |_| \__||_||_| |_| |_|\__,_|  \__|\___|

================================================================================
  PROGRAM DIRECTOR: ChiTownSounds aka Will
  SYSTEM: FMP Ultimate Download System
  STATUS: Initializing...
================================================================================""" + "\033[0m"

    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        print(red_banner)
        
    state.update_count()
    start_engines()
    app.run(host='127.0.0.1', port=5000, debug=False)