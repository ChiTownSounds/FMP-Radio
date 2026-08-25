import os
import subprocess
import logging
import re
import time
import platform
import requests
from typing import Tuple
from config import STAGING_DIR, SOMEDL_CMD, YT_DLP_CMD

class Transporter:
    def __init__(self):
        os.makedirs(STAGING_DIR, exist_ok=True)
        # Load Soulseek configuration from environment
        self.slskd_url = os.getenv("SLSKD_URL", "http://127.0.0.1:5030").rstrip('/')
        self.slskd_api_key = os.getenv("SLSKD_API_KEY", "")
        self.slsk_user = os.getenv("SLSK_USERNAME", "dj_al_g_rhythm")
        self.slsk_pass = os.getenv("SLSK_PASSWORD", "")
        self.slsk_port = os.getenv("SLSK_FORWARDED_PORT", "48152")

    def _log_to_system(self, message: str):
        """Helper to log to the central SystemState without top-level circular imports."""
        try:
            from app import state
            state.log(message)
        except ImportError:
            logging.error(f"[SYSTEM-LOG-FAILED] {message}")

    def download_track(self, url: str, task_id: str = "temp") -> Tuple[str, str]:
        """Downloads audio using Soulseek (slskd) if configured, with fallbacks to SomeDL/yt-dlp."""
        # Check if Soulseek is configured and query is a search query
        is_slsk_ready = (
            self.slsk_user and 
            self.slsk_user != "YOUR_SOULSEEK_USERNAME" and
            self.slskd_api_key and 
            self.slskd_api_key != "YOUR_SECURE_SLSKD_API_KEY"
        )
        
        is_search = url.startswith("ytmsearch1:") or url.startswith("ytsearch1:")
        
        if is_slsk_ready and is_search:
            query = url.split(":", 1)[1]
            # Strip standard YouTube clean suffixes from query to avoid breaking search
            query_clean = re.sub(r'\s*\(Clean\)\s*', '', query, flags=re.IGNORECASE)
            query_clean = re.sub(r'\s*\[Clean\]\s*', '', query_clean, flags=re.IGNORECASE)
            # Remove underscores and replacement characters (user rule)
            query_clean = query_clean.replace('_', ' ').replace('\uFFFD', ' ')
            
            self._log_to_system(f"[SOULSEEK] Searching Soulseek for: \"{query_clean}\"")
            try:
                raw_path, bitrate = self._download_via_slskd(query_clean, task_id)
                if raw_path:
                    self._log_to_system(f"[SUCCESS] Soulseek download complete: {raw_path}")
                    return raw_path, bitrate
                else:
                    self._log_to_system(f"[WARNING] Soulseek found no suitable matches for \"{query_clean}\". Falling back to web scrapers.")
            except Exception as e:
                self._log_to_system(f"[ERROR] Soulseek download failed: {e}. Falling back to web scrapers.")

        # --- FALLBACK DOWN-LEVEL WEB INGESTION (OLD LOGIC) ---
        # Normalize music.youtube.com watch URLs to www.youtube.com to bypass SomeDL parser bugs
        if "music.youtube.com/watch" in url:
            url = url.replace("music.youtube.com/watch", "www.youtube.com/watch")
            
        staging_path = os.path.join(STAGING_DIR, task_id)
        os.makedirs(staging_path, exist_ok=True)

        # 1. Primary Attempt: SomeDL
        cmd = SOMEDL_CMD + ["-f", "mp3", "-o", staging_path, url]
        
        try:
            logging.info(f"Running SomeDL: {' '.join(cmd)}")
            res = subprocess.run(cmd, check=True, capture_output=True, text=True, encoding='utf-8', timeout=300)
            
            # Find the downloaded file
            files = [f for f in os.listdir(staging_path) if f.endswith('.mp3')]
            if files:
                return os.path.join(staging_path, files[0]), "320"
            else:
                self._log_to_system(f"[WARNING] SomeDL finished but staging is empty. stdout: {res.stdout.strip()[:200]} | stderr: {res.stderr.strip()[:200]}")
                
        except subprocess.TimeoutExpired:
            self._log_to_system(f"[TIMEOUT] SomeDL stalled for >300s on {url}. Killing process.")
        except subprocess.CalledProcessError as e:
            logging.error(f"SomeDL failed: {e.stderr}")
            self._log_to_system(f"[ERROR] SomeDL failed on {url}. stdout: {e.stdout.strip()[:200]} | stderr: {e.stderr.strip()[:200]}")
        except Exception as e:
            logging.error(f"Unexpected error in SomeDL: {e}")
            self._log_to_system(f"[ERROR] Unexpected error in SomeDL: {e}")

        # 2. Secondary Attempt: yt-dlp Fallback
        self._log_to_system(f"[FALLBACK] SomeDL failed. Attempting yt-dlp download for {url}...")
        
        yt_dlp_cmd = YT_DLP_CMD + [
            "-x", 
            "--audio-format", "mp3", 
            "--audio-quality", "320K", 
            "-o", os.path.join(staging_path, "%(title)s.%(ext)s"), 
            url
        ]
        
        try:
            logging.info(f"Running yt-dlp fallback: {' '.join(yt_dlp_cmd)}")
            res = subprocess.run(yt_dlp_cmd, check=True, capture_output=True, text=True, encoding='utf-8', timeout=300)
            
            stderr_lower = (res.stderr or "").lower()
            stdout_lower = (res.stdout or "").lower()
            if "cookies are no longer valid" in stderr_lower or "cookies are no longer valid" in stdout_lower:
                self._log_to_system("[ERROR] YouTube cookies have expired or are invalid. Aborting download.")
                return "", ""
                
            files = [f for f in os.listdir(staging_path) if f.endswith('.mp3')]
            if files:
                self._log_to_system(f"[SUCCESS] yt-dlp fallback download completed for {url}.")
                return os.path.join(staging_path, files[0]), "320"
            else:
                self._log_to_system(f"[WARNING] yt-dlp finished but staging is empty. stdout: {res.stdout.strip()[:200]} | stderr: {res.stderr.strip()[:200]}")
                
        except subprocess.TimeoutExpired:
            self._log_to_system(f"[TIMEOUT] yt-dlp stalled for >300s on {url}. Killing process.")
        except subprocess.CalledProcessError as e:
            logging.error(f"yt-dlp fallback failed: {e.stderr}")
            stderr_lower = (e.stderr or "").lower()
            stdout_lower = (e.stdout or "").lower()
            if "confirm you’re not a bot" in stderr_lower or "confirm you’re not a bot" in stdout_lower:
                self._log_to_system("[ERROR] YouTube bot block detected. Aborting download.")
            elif "cookies are no longer valid" in stderr_lower or "cookies are no longer valid" in stdout_lower:
                self._log_to_system("[ERROR] YouTube cookies have expired or are invalid. Aborting download.")
            else:
                self._log_to_system(f"[ERROR] yt-dlp fallback failed on {url}. stdout: {e.stdout.strip()[:200]} | stderr: {e.stderr.strip()[:200]}")
        except Exception as e:
            logging.error(f"Unexpected error in yt-dlp: {e}")
            self._log_to_system(f"[ERROR] Unexpected error in yt-dlp: {e}")

        return "", ""

    def _download_via_slskd(self, query: str, task_id: str) -> Tuple[str, str]:
        """Queries slskd REST API, handles file evaluation, starts download, and pulls the resulting file."""
        headers = {
            "X-API-Key": self.slskd_api_key,
            "Content-Type": "application/json"
        }
        
        # 1. Initiate search on Soulseek network
        search_payload = {"searchText": query}
        search_res = requests.post(f"{self.slskd_url}/api/v0/searches", json=search_payload, headers=headers, timeout=15)
        if search_res.status_code not in (200, 201):
            logging.error(f"Failed to initiate Soulseek search: {search_res.status_code} - {search_res.text}")
            return "", ""
            
        search_id = search_res.json().get("id")
        if not search_id:
            logging.error("No search ID returned by slskd searches API")
            return "", ""
            
        # 2. Poll for search responses (gather for ~15 seconds to let peers reply)
        self._log_to_system(f"[SOULSEEK] Search submitted (ID: {search_id}). Gathering results...")
        best_file = None
        best_score = -1
        
        for poll_idx in range(6):
            time.sleep(2.5)
            res = requests.get(f"{self.slskd_url}/api/v0/searches/{search_id}/responses", headers=headers, timeout=15)
            if res.status_code != 200:
                continue
                
            responses = res.json()
            if not responses:
                continue
                
            for resp in responses:
                username = resp.get("username")
                files = resp.get("files", [])
                slots = resp.get("slots", True)
                speed = resp.get("speed", 0)
                
                for file_info in files:
                    filename = file_info.get("filename")
                    size = file_info.get("size", 0)
                    bitrate = file_info.get("bitRate", 0)
                    ext = file_info.get("extension", "").lower()
                    
                    if ext not in ("mp3", "flac") or size < 1000000:
                        continue
                        
                    # Evaluate file suitability
                    score = 0
                    if ext == "flac":
                        score += 30
                    elif bitrate >= 320:
                        score += 20
                    elif bitrate >= 256:
                        score += 10
                    else:
                        score += 1
                        
                    if slots:
                        score += 5
                    score += min(5, speed / 1000000)
                    
                    fn_lower = filename.lower()
                    if "clean" in query.lower():
                        if "clean" in fn_lower or "radio edit" in fn_lower or "radio_edit" in fn_lower or "edit" in fn_lower:
                            score += 15
                    elif "explicit" in query.lower():
                        if "explicit" in fn_lower or "uncut" in fn_lower or "dirty" in fn_lower:
                            score += 15
                            
                    if "sample" in fn_lower or "preview" in fn_lower or "promo" in fn_lower:
                        score -= 20
                        
                    if score > best_score:
                        best_score = score
                        best_file = {
                            "username": username,
                            "filename": filename,
                            "size": size,
                            "bitrate": str(bitrate) if ext == "mp3" else "FLAC",
                            "extension": ext
                        }
        
        # Clean up search session on the server
        try:
            requests.delete(f"{self.slskd_url}/api/v0/searches/{search_id}", headers=headers, timeout=10)
        except Exception:
            pass
            
        if not best_file:
            return "", ""
            
        self._log_to_system(f"[SOULSEEK] Selected best match from peer '{best_file['username']}': {os.path.basename(best_file['filename'])} ({best_file['bitrate']})")
        
        # 3. Enqueue download from selected peer
        download_payload = {
            "files": [best_file["filename"]]
        }
        dl_res = requests.post(
            f"{self.slskd_url}/api/v0/transfers/downloads/{best_file['username']}", 
            json=download_payload, 
            headers=headers, 
            timeout=15
        )
        if dl_res.status_code not in (200, 201, 202):
            logging.error(f"Failed to queue download: {dl_res.status_code} - {dl_res.text}")
            return "", ""
            
        # 4. Monitor transfer progress
        self._log_to_system(f"[SOULSEEK] Download queued. Monitoring transfer...")
        start_time = time.time()
        timeout_limit = 600
        local_rel_path = ""

        # Two-phase timeout:
        #   Phase 1 (Init): If percentComplete stays at 0 for >25s, the peer is
        #                   queued but not sending — abort and fall through to SomeDL.
        #   Phase 2 (Active): Once any progress is detected, allow the full 600s window.
        init_deadline = time.time() + 25
        active_started = False

        while time.time() - start_time < timeout_limit:
            time.sleep(5)
            status_res = requests.get(f"{self.slskd_url}/api/v0/transfers/downloads/{best_file['username']}", headers=headers, timeout=15)
            if status_res.status_code != 200:
                continue
                
            downloads = status_res.json()
            target_dl = None
            for dl in downloads:
                if dl.get("filename") == best_file["filename"]:
                    target_dl = dl
                    break
                    
            if not target_dl:
                # If cleared or missing, it might have finished and auto-cleared. Check below.
                break
                
            state = target_dl.get("state", "")
            percent = target_dl.get("percentComplete", 0)

            # Once any bytes are flowing, mark transfer as active
            if percent > 0:
                active_started = True

            # Phase 1 watchdog: kill stuck-queued transfer before wasting 10 minutes
            if not active_started and time.time() > init_deadline:
                self._log_to_system(
                    f"[SOULSEEK] Init timeout: peer '{best_file['username']}' queued "
                    f"but 0% progress after 25s. Aborting and falling through to SomeDL."
                )
                try:
                    requests.delete(
                        f"{self.slskd_url}/api/v0/transfers/downloads/{best_file['username']}",
                        headers=headers, timeout=10
                    )
                except Exception:
                    pass
                return "", ""
            
            # Extract container's download path if available
            if "localPath" in target_dl:
                local_rel_path = target_dl["localPath"]
            elif "path" in target_dl:
                local_rel_path = target_dl["path"]
                
            if state == "Completed" or percent >= 100:
                break
            elif state in ("Errored", "Cancelled", "TimedOut", "RemoteCaborted"):
                self._log_to_system(f"[ERROR] Soulseek download aborted: state={state}")
                return "", ""
                
        # 5. Resolve host path and transport file
        # Default fallback structure inside container: /app/downloads/completed/username/filename
        if not local_rel_path:
            # Fallback path prediction based on standard structure
            local_rel_path = f"/app/downloads/completed/{best_file['username']}/{os.path.basename(best_file['filename'])}"
            
        # Translate the container internal path back to the VM host path
        host_path_on_vm = local_rel_path.replace('/app/downloads', '/home/ubuntu/music/staging')
        
        if platform.system() != "Windows":
            # Running directly on OCI VM: access files locally
            if os.path.exists(host_path_on_vm):
                return host_path_on_vm, best_file["bitrate"]
            else:
                # Direct check without subdirectories if it nested differently
                alt_path = os.path.join("/home/ubuntu/music/staging", os.path.basename(best_file["filename"]))
                if os.path.exists(alt_path):
                    return alt_path, best_file["bitrate"]
                logging.error(f"Download reported complete, but file not found on OCI disk: {host_path_on_vm}")
                return "", ""
        else:
            # Running locally on Windows: SCP the file down from OCI VM to local Windows staging directory
            ssh_key = "C:/Users/chito/.ssh/id_ed25519"
            remote_ip = "ultimate.fmpmediagroup.com"
            broadcaster_env = "C:/FMP_Broadcaster/.env"
            if os.path.exists(broadcaster_env):
                try:
                    with open(broadcaster_env, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.strip().startswith("REMOTE_VM_IP="):
                                remote_ip = line.strip().split("=", 1)[1].strip()
                except Exception:
                    pass
                    
            local_staging_dir = os.path.join(STAGING_DIR, task_id)
            os.makedirs(local_staging_dir, exist_ok=True)
            local_dest = os.path.join(local_staging_dir, os.path.basename(best_file["filename"]))
            
            # scp command to pull file from cloud down to local Windows
            scp_cmd = [
                "scp", 
                "-o", "BatchMode=yes", 
                "-o", "StrictHostKeyChecking=accept-new", 
                "-i", ssh_key, 
                f"ubuntu@{remote_ip}:{host_path_on_vm}", 
                local_dest
            ]
            self._log_to_system(f"[SOULSEEK] Transferring downloaded file from VM to Windows: {os.path.basename(best_file['filename'])}")
            
            res = subprocess.run(scp_cmd, capture_output=True, text=True, encoding="utf-8")
            if res.returncode == 0 and os.path.exists(local_dest):
                return local_dest, best_file["bitrate"]
            else:
                # Try fallback location check (sometimes files end up directly under staging directory)
                alt_host_path = f"/home/ubuntu/music/staging/{os.path.basename(best_file['filename'])}"
                scp_cmd[-2] = f"ubuntu@{remote_ip}:{alt_host_path}"
                res = subprocess.run(scp_cmd, capture_output=True, text=True, encoding="utf-8")
                if res.returncode == 0 and os.path.exists(local_dest):
                    return local_dest, best_file["bitrate"]
                    
                self._log_to_system(f"[ERROR] SCP transport failed. stdout: {res.stdout.strip()} | stderr: {res.stderr.strip()}")
                return "", ""