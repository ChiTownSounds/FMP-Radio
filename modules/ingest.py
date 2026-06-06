import subprocess
import json
import logging
from typing import Tuple, Dict
from config import YT_DLP_CMD

class Gatekeeper:
    def process_request(self, url: str) -> Tuple[bool, Dict]:
        """Validates the URL and extracts initial metadata."""
        if not url:
            return False, {"error": "Empty URL"}

        # Basic sanitization
        url = url.strip()
        
        # Strict URL Validation Veto
        is_youtube = "youtube.com" in url or "youtu.be" in url
        is_yt_music = "music.youtube.com" in url
        
        if is_youtube and not is_yt_music:
            return False, {"error": "Standard YouTube video links are strictly prohibited. Use YouTube Music links only.", "veto": True}

        # Phase 1: Metadata Extraction via yt-dlp
        cmd = YT_DLP_CMD + ["-j", "--flat-playlist", url]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            meta = json.loads(result.stdout)
            
            # Extract relevant fields (lyrics is set to 'Not Found' since SomeDL handles it)
            data = {
                "title": meta.get("title", "Unknown Title"),
                "artist": meta.get("uploader", "Unknown Artist"),
                "release_year": meta.get("release_year") or meta.get("upload_date")[:4] if meta.get("upload_date") else "Unknown",
                "duration": meta.get("duration", 0),
                "lyrics": "Not Found",
                "url": url
            }
            
            return True, data

        except subprocess.CalledProcessError as e:
            logging.error(f"Validation failed for {url}: {e.stderr}")
            try:
                from app import state
                state.log("[WARNING] Gatekeeper blind... Forcing SomeDL override.")
            except ImportError:
                print("[WARNING] Gatekeeper blind... Forcing SomeDL override.")
            return True, {
                "title": "Unknown Title",
                "artist": "Unknown Artist",
                "release_year": "Unknown",
                "duration": 0,
                "lyrics": "Not Found",
                "url": url,
                "_blind_override": True
            }
        except Exception as e:
            logging.error(f"Unexpected error in Gatekeeper: {e}")
            return False, {"error": str(e)}