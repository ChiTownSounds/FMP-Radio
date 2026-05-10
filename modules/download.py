import subprocess
import logging
import os
import glob
from typing import Optional

from config import SOMEDL_CMD, STAGING_DIR

class Transporter:
    def __init__(self):
        if not os.path.exists(STAGING_DIR):
            os.makedirs(STAGING_DIR)

    def download_track(self, url: str) -> Optional[str]:
        # Clean the staging directory before downloading to prevent race conditions
        for f in glob.glob(os.path.join(STAGING_DIR, "*.mp3")):
            try: os.remove(f)
            except OSError: pass

        cmd = SOMEDL_CMD + [
            url,
            "--format", "mp3",
            "--output", str(STAGING_DIR),
            "--no-album",
            "--skip-file-check"
        ]

        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            # Find the new file SomeDL just created
            staged_files = glob.glob(os.path.join(STAGING_DIR, "*.mp3"))
            if staged_files:
                return staged_files[0]
            return None

        except subprocess.CalledProcessError:
            return None
        except FileNotFoundError:
            return None