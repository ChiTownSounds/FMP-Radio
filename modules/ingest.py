import subprocess
import json
import logging
import re
from typing import Tuple, Dict
from config import YT_DLP_CMD

class Gatekeeper:
    def _clean_text(self, text: str) -> str:
        """Removes HTML artifacts and normalizes whitespace."""
        if not text:
            return "Not Found"
        # Strip HTML tags
        clean = re.sub(r'<[^>]*>', '', text)
        # Normalize whitespace
        clean = " ".join(clean.split())
        return clean if clean else "Not Found"

    def process_request(self, url: str) -> Tuple[bool, Dict]:
        """Validates the URL and extracts initial metadata including lyrics."""
        if not url:
            return False, {"error": "Empty URL"}

        # Basic sanitization
        url = url.strip()
        
        # Phase 1: Metadata Extraction via yt-dlp
        cmd = YT_DLP_CMD + ["-j", "--flat-playlist", url]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            meta = json.loads(result.stdout)
            
            # [TRUE LYRICS INGESTION]
            # Check lyrics field first, fallback to description
            raw_lyrics = meta.get("lyrics") or meta.get("description")
            clean_lyrics = self._clean_text(raw_lyrics)

            # Extract relevant fields
            data = {
                "title": meta.get("title", "Unknown Title"),
                "artist": meta.get("uploader", "Unknown Artist"),
                "release_year": meta.get("release_year") or meta.get("upload_date")[:4] if meta.get("upload_date") else "Unknown",
                "duration": meta.get("duration", 0),
                "lyrics": clean_lyrics,
                "url": url
            }
            
            return True, data

        except subprocess.CalledProcessError as e:
            logging.error(f"Validation failed for {url}: {e.stderr}")
            return False, {"error": "Invalid URL or metadata inaccessible"}
        except Exception as e:
            logging.error(f"Unexpected error in Gatekeeper: {e}")
            return False, {"error": str(e)}