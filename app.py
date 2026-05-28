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
from ftplib import FTP

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

class SystemState:
    def __init__(self):
        self.url_queue = queue.Queue()
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
        self.start_spinner()

    def update_count(self):
        with self.lock:
            self.total_in_queue = self.url_queue.qsize() + self.vault_queue.qsize()

    def log(self, message: str):
        with self.lock:
            self.logs.append(message)
            sys.stdout.write(f"\n[SYSTEM] {message}\n")
            sys.stdout.flush()

    def set_status(self, status: str, url: str = None):
        with self.lock:
            self.current_status = status
            if url is not None:
                self.current_track_url = url

    def increment_completed(self):
        with self.lock:
            self.completed_count += 1

    def get_snapshot(self):
        with self.lock:
            return {
                "total_in_queue": self.total_in_queue,
                "completed_count": self.completed_count,
                "current_status": self.current_status,
                "current_track_url": self.current_track_url,
                "logs": list(self.logs)
            }

    def start_spinner(self):
        def spin():
            spinner = itertools.cycle(['|', '/', '-', '\\'])
            while True:
                with self.lock:
                    status = self.current_status
                    last_log = self.logs[-1] if self.logs else 'Processing'
                
                if status == "Running":
                    sys.stdout.write(f"\r[WORKING] {last_log} {next(spinner)}".ljust(80))
                    sys.stdout.flush()
                time.sleep(0.15)
        threading.Thread(target=spin, daemon=True).start()

state = SystemState()

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
        target_override = task.get('target')
        
        state.set_status("Running", url)
        task_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        staging_task_dir = os.path.join(STAGING_DIR, task_id)
        
        gk, tr, am = Gatekeeper(), Transporter(), AutoMaster()
        handoff_success = False

        try:
            state.log(f"Phase 1: Validating {url}")
            v, meta = gk.process_request(url)
            
            # THE GATEKEEPER BLIND PASS-THROUGH
            if not v:
                state.log(f"[WARNING] Gatekeeper blind: {meta.get('error', 'Unknown')}. Forcing SomeDL override.")
                meta = {'release_year': 'Unknown', 'lyrics': 'Not Found', 'url': url}

            state.log(f"Phase 2: Downloading via SomeDL")
            raw_path, bitrate = tr.download_track(url, task_id=task_id)
            
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
            
            if target_override:
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

# --- WEB ROUTES ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/shows')
def get_shows():
    try:
        ftp = FTP()
        ftp.connect(FTP_HOST, FTP_PORT, timeout=15)
        ftp.login(FTP_USER, FTP_PASS)
        ftp.set_pasv(True)
        try:
            ftp.cwd(f"{FTP_BASE_DIR}/Shows")
            shows = [item for item in ftp.nlst() if not item.endswith('.mp3')]
        except:
            shows = [] 
        ftp.quit()
        return jsonify(shows)
    except Exception as e:
        state.log(f"[ERROR] Could not fetch shows from FTP: {e}")
        logging.error(f"FTP Shows fetch error: {e}")
        return jsonify([])

@app.route('/api/new_show', methods=['POST'])
def new_show():
    show_name = request.json.get('show_name')
    if show_name:
        safe_name = "".join(c for c in show_name if c.isalnum() or c in (' ', '_', '-')).strip()
        try:
            ftp = FTP()
            ftp.connect(FTP_HOST, FTP_PORT, timeout=15)
            ftp.login(FTP_USER, FTP_PASS)
            ftp.set_pasv(True)
            try: ftp.cwd(f"{FTP_BASE_DIR}/Shows")
            except: ftp.cwd(FTP_BASE_DIR); ftp.mkd("Shows"); ftp.cwd("Shows")
            
            ftp.mkd(safe_name) 
            ftp.quit()
        except Exception as e:
            state.log(f"[ERROR] Failed to create remote show folder: {e}")
            logging.error(f"Remote folder creation failed for {safe_name}: {e}")
    return jsonify({"status": "ok"})

@app.route('/add', methods=['POST'])
def add():
    raw_urls = request.json.get('urls', '').split('\n')
    target = request.json.get('target', '')
    for u in raw_urls:
        u = u.strip()
        if not u: continue
        if "list=" in u or "playlist" in u.lower():
            for track_url in extract_playlist_urls(u): 
                state.url_queue.put({'url': track_url, 'type': 'ingest', 'target': target})
        else: 
            state.url_queue.put({'url': u, 'type': 'ingest', 'target': target})
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
    
    def process_local():
        am = AutoMaster()
        state.set_status("Running")
        handoff_success = False
        try:
            state.log(f"Phase 2.5: Auto-Mastering Local Upload [{file.filename}]")
            mastered_path, updates = am.process_file(raw_path)
            
            if not mastered_path:
                handoff_success = False
                return
            
            meta = {'abr': 'Local', 'release_year': 'Unknown'}
            meta.update(updates)
            
            # Era-to-Energy Category Folder Interception
            release_year = meta.get('release_year', 'Unknown')
            clean_name = os.path.basename(mastered_path)
            
            if target:
                era_folder = target
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

            state.vault_queue.put({'path': mastered_path, 'meta': meta, 'task_id': task_id, 'target': target})
            handoff_success = True
            state.update_count()
        except Exception as e:
            state.log(f"[LOCAL UPLOAD ERROR] {e}")
            logging.exception(f"Local upload crash for {file.filename}: {e}")
        finally:
            if not handoff_success:
                if os.path.exists(staging_path):
                    shutil.rmtree(staging_path, ignore_errors=True)
        
    threading.Thread(target=process_local, daemon=True).start()
    start_engines()
    return jsonify({"status": "ok"})

@app.route('/stop', methods=['POST'])
def stop(): 
    state.stop_event.set()
    state.set_status("Stopping...")
    return jsonify({"status": "ok"})

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
        
    app.run(host='127.0.0.1', port=5000, debug=True)