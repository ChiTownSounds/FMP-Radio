import os
import subprocess
import logging
from typing import Tuple
from config import STAGING_DIR, SOMEDL_CMD, YT_DLP_CMD

class Transporter:
    def __init__(self):
        os.makedirs(STAGING_DIR, exist_ok=True)

    def _log_to_system(self, message: str):
        """Helper to log to the central SystemState without top-level circular imports."""
        try:
            from app import state
            state.log(message)
        except ImportError:
            logging.error(f"[SYSTEM-LOG-FAILED] {message}")

    def download_track(self, url: str, task_id: str = "temp") -> Tuple[str, str]:
        """Downloads audio using SomeDL with fallback to yt-dlp, enforcing strict timeouts."""
        # Normalize music.youtube.com watch URLs to www.youtube.com to bypass SomeDL parser bugs
        if "music.youtube.com/watch" in url:
            url = url.replace("music.youtube.com/watch", "www.youtube.com/watch")
            
        staging_path = os.path.join(STAGING_DIR, task_id)
        os.makedirs(staging_path, exist_ok=True)

        # 1. Primary Attempt: SomeDL
        cmd = SOMEDL_CMD + ["-f", "mp3", "-o", staging_path, url]
        
        try:
            logging.info(f"Running SomeDL: {' '.join(cmd)}")
            # Enforce 300s timeout to prevent thread hangs
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
        
        # Enforce mp3 extraction and 320k quality
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
            
            # Check for invalid cookies warning in stdout/stderr
            stderr_lower = (res.stderr or "").lower()
            stdout_lower = (res.stdout or "").lower()
            if "cookies are no longer valid" in stderr_lower or "cookies are no longer valid" in stdout_lower:
                self._log_to_system("[ERROR] YouTube cookies have expired or are invalid. Aborting download to prevent low-quality fallback.")
                return "", ""
                
            # Find the downloaded file
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
                self._log_to_system("[ERROR] YouTube bot block detected (Sign in to confirm you are not a bot). Aborting download.")
            elif "cookies are no longer valid" in stderr_lower or "cookies are no longer valid" in stdout_lower:
                self._log_to_system("[ERROR] YouTube cookies have expired or are invalid. Aborting download.")
            else:
                self._log_to_system(f"[ERROR] yt-dlp fallback failed on {url}. stdout: {e.stdout.strip()[:200]} | stderr: {e.stderr.strip()[:200]}")
        except Exception as e:
            logging.error(f"Unexpected error in yt-dlp: {e}")
            self._log_to_system(f"[ERROR] Unexpected error in yt-dlp: {e}")

        return "", ""