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
        staging_path = os.path.join(STAGING_DIR, task_id)
        os.makedirs(staging_path, exist_ok=True)

        # 1. Primary Attempt: SomeDL
        cmd = SOMEDL_CMD + ["-f", "mp3", "-o", staging_path, url]
        
        try:
            logging.info(f"Running SomeDL: {' '.join(cmd)}")
            # Enforce 300s timeout to prevent thread hangs
            res = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
            
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

        return "", ""