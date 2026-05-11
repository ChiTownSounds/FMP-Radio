import sys
import logging
from flask import Flask, render_template, request, jsonify
import threading
import queue
import time
import itertools
import yt_dlp
from thefuzz import fuzz

# --- THE SILENCER ---
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

from modules.ingest import Gatekeeper
from modules.download import Transporter
from modules.storage import VaultManager

app = Flask(__name__)

class SystemState:
    def __init__(self):
        self.url_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = None
        self.total_in_queue = 0
        self.completed_count = 0
        self.current_status = "Idle"
        self.current_track_url = "None"
        self.logs = ["FMP ULTIMATE ONLINE."]
        self.start_spinner()

    def log(self, message: str):
        self.logs.append(message)
        if len(self.logs) > 10: self.logs.pop(0)
        
        if any(x in message for x in ["Complete", "Rejected", "ERASED", "failed", "Phase", "UPGRADE", "GUARDRAIL", "OVERRIDE"]):
            sys.stdout.write(f"\n[SYSTEM] {message}\n")
            sys.stdout.flush()

    def start_spinner(self):
        def spin():
            spinner = itertools.cycle(['|', '/', '-', '\\'])
            while True:
                if self.current_status == "Running":
                    current_msg = self.logs[-1] if self.logs else "Processing"
                    sys.stdout.write(f"\r[WORKING] {current_msg} {next(spinner)}".ljust(80))
                    sys.stdout.flush()
                time.sleep(0.15)
        threading.Thread(target=spin, daemon=True).start()

state = SystemState()

def pipeline_worker():
    print("\n" + "="*50)
    print("FMP ULTIMATE CLEAN ROOM: ENGINE INITIALIZED")
    print("="*50 + "\n")
    
    while not state.stop_event.is_set():
        try: 
            task = state.url_queue.get(timeout=1)
        except queue.Empty: 
            state.current_status = "Idle"
            continue

        # We instantiate fresh tools for EVERY task to guarantee zero stale memory
        gk, tr, vm = Gatekeeper(), Transporter(), VaultManager()

        url = task.get('url')
        task_type = task.get('type', 'ingest')
        old_name = task.get('old_name', '')

        state.current_status = "Running"
        state.current_track_url = url
        
        # ==========================================
        # THE GUARDRAIL: Advance Scout Protocol
        # ==========================================
        if task_type == 'upgrade':
            state.log(f"[GUARDRAIL] Scouting metadata for: {old_name}")
            try:
                with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
                    info = ydl.extract_info(url, download=False)
                    new_artist = info.get('artist') or info.get('uploader', 'Unknown Artist')
                    new_title = info.get('track') or info.get('title', 'Unknown Title')
                    new_song_id = f"{new_artist} - {new_title}"
                    
                match_score = fuzz.token_set_ratio(new_song_id.lower(), old_name.lower())
                
                if match_score < 45: 
                    state.log(f"[GUARDRAIL BLOCKED] Mismatch! '{old_name}' vs '{new_song_id}' (Score: {match_score})")
                    state.total_in_queue = state.url_queue.qsize()
                    state.url_queue.task_done()
                    continue
                else:
                    state.log(f"[GUARDRAIL PASSED] Match confirmed. Clearing path...")
                    vm.scrub_track(old_name)
            except Exception as e:
                state.log(f"[GUARDRAIL ERROR] Failed to scout URL: {e}")
                state.total_in_queue = state.url_queue.qsize()
                state.url_queue.task_done()
                continue

        # ==========================================
        # PHASE 1: Gatekeeper & Executive Override
        # ==========================================
        state.log(f"Phase 1: Validating {url}")
        
        v, meta = gk.process_request(url)
        
        if not v:
            err_msg = meta.get('error', '')
            # THE OVERRIDE: If the Gatekeeper complains about CSV/Upgrades, we overrule it.
            if task_type == 'upgrade' and 'CSV' in err_msg:
                state.log(f"[EXECUTIVE OVERRIDE] Gatekeeper bypassed. Forcing authorized upgrade.")
            else:
                state.log(f"Rejected: {err_msg}")
                state.total_in_queue = state.url_queue.qsize()
                state.url_queue.task_done()
                continue

        song_id = f"{meta.get('artist', 'Unknown')} - {meta.get('title', 'Unknown')}"
        state.log(f"Phase 2: Downloading [{song_id}]")
        
        path, final_bitrate = tr.download_track(url)
        
        if not path:
            state.log(f"Phase 2 Error: Download failed for {song_id}")
            state.total_in_queue = state.url_queue.qsize()
            state.url_queue.task_done()
            continue

        meta['abr'] = final_bitrate

        state.log(f"Phase 3: Vaulting [{song_id}] to Z:\\")
        if vm.store_track(path, meta):
            state.log(f"Pipeline Complete: {song_id}")
            state.completed_count += 1
        else: 
            state.log(f"Phase 3 Error: Vault failed for {song_id}")
            
        state.total_in_queue = state.url_queue.qsize()
        state.url_queue.task_done()

    state.current_status = "Stopped"
    print("\n[SYSTEM] ENGINE SHUTDOWN COMPLETE.\n")

@app.route('/')
def index(): return render_template('index.html')

@app.route('/add', methods=['POST'])
def add_urls():
    urls = [u.strip() for u in request.json.get('urls', '').split('\n') if u.strip()]
    for u in urls: 
        state.url_queue.put({'url': u, 'type': 'ingest'})
    state.total_in_queue = state.url_queue.qsize()
    
    if state.worker_thread is None or not state.worker_thread.is_alive():
        state.stop_event.clear()
        state.worker_thread = threading.Thread(target=pipeline_worker, daemon=True)
        state.worker_thread.start()
    return jsonify({"status": "ok"})

@app.route('/stop', methods=['POST'])
def stop_worker():
    state.stop_event.set()
    state.current_status = "Stopping..."
    return jsonify({"status": "stopping"})

@app.route('/search_scrub', methods=['POST'])
def search_scrub():
    q = request.json.get('query', '')
    return jsonify(VaultManager().find_candidates(q))

@app.route('/execute_scrub', methods=['POST'])
def execute_scrub():
    name = request.json.get('track_name', '')
    s, m = VaultManager().scrub_track(name)
    state.log(f"[ERASED] {name} - {m}" if s else f"[FAILED] {m}")
    return jsonify({"status": "ok"})

@app.route('/api/qc_scan')
def qc_scan():
    weak_links = VaultManager().get_weak_links(threshold_kbps=256)
    return jsonify(weak_links)

@app.route('/api/upgrade', methods=['POST'])
def upgrade_track():
    old_name = request.json.get('old_track_name', '')
    new_url = request.json.get('new_url', '').strip()
    
    if not old_name or not new_url:
        return jsonify({"status": "error", "message": "Missing data."})

    state.url_queue.put({
        'url': new_url, 
        'type': 'upgrade', 
        'old_name': old_name
    })
    state.total_in_queue = state.url_queue.qsize()
    
    if state.worker_thread is None or not state.worker_thread.is_alive():
        state.stop_event.clear()
        state.worker_thread = threading.Thread(target=pipeline_worker, daemon=True)
        state.worker_thread.start()

    return jsonify({"status": "ok", "message": "Upgrade initiated. Awaiting Guardrail..."})

@app.route('/api/status')
def get_status():
    return jsonify({
        "total_in_queue": state.total_in_queue, 
        "completed_count": state.completed_count, 
        "current_status": state.current_status, 
        "current_track_url": state.current_track_url, 
        "logs": state.logs
    })

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)