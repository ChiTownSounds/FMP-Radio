import subprocess
import json
import csv
import logging
from typing import Tuple, Dict, Any

from config import YT_DLP_CMD, CSV_BLUEPRINT

class Gatekeeper:
    def __init__(self):
        self.csv_data = self._load_blueprint()
        self.blocklist = ["official video", "intro", "sfx", "commercial"]

    def _load_blueprint(self) -> Dict[str, dict]:
        db = {}
        try:
            with open(CSV_BLUEPRINT, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    track_name = row.get('Track Name', '').strip().lower()
                    if track_name:
                        db[track_name] = row
        except FileNotFoundError:
            logging.warning("CSV Blueprint not found. Assuming empty database.")
        return db

    def fetch_metadata(self, url: str) -> dict:
        cmd = YT_DLP_CMD + ["--dump-json", url]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        except subprocess.CalledProcessError as e:
            return {}
        except json.JSONDecodeError:
            return {}

    def passes_firewall(self, metadata: dict) -> bool:
        title = metadata.get('title', '').lower()
        description = metadata.get('description', '').lower()
        for word in self.blocklist:
            if word in title or word in description:
                return False
        return True

    def is_upgrade(self, yt_meta: dict, csv_record: dict) -> bool:
        yt_bitrate = yt_meta.get('abr', 0)
        try:
            csv_bitrate = float(csv_record.get('Bitrate', 0))
        except ValueError:
            csv_bitrate = 0.0
            
        if yt_bitrate > csv_bitrate: return True

        csv_lyrics = str(csv_record.get('Lyrics', '')).strip().lower()
        yt_subtitles = yt_meta.get('subtitles') or yt_meta.get('automatic_captions')
        if csv_lyrics in ['false', 'no', ''] and yt_subtitles: return True

        csv_year = str(csv_record.get('Year', '')).strip()
        yt_year = yt_meta.get('release_year') or yt_meta.get('upload_date')
        if not csv_year and yt_year: return True

        thumbnails = yt_meta.get('thumbnails', [])
        if thumbnails:
            best_thumb = thumbnails[-1] 
            width, height = best_thumb.get('width', 0), best_thumb.get('height', 0)
            if width and height:
                yt_ratio = width / height
                yt_ratio_diff = abs(1.0 - yt_ratio)
                try:
                    csv_ratio = float(csv_record.get('Art Ratio', 0))
                    csv_ratio_diff = abs(1.0 - csv_ratio)
                except ValueError:
                    csv_ratio_diff = 999.0 
                if yt_ratio_diff < csv_ratio_diff: return True

        return False

    def process_request(self, url: str) -> Tuple[bool, Dict[str, Any]]:
        metadata = self.fetch_metadata(url)
        if not metadata: return False, {"error": "Failed to retrieve metadata"}
        if not self.passes_firewall(metadata): return False, {"error": "Blocked by Content Firewall"}

        yt_title = metadata.get('title', '').strip().lower()
        if yt_title in self.csv_data:
            if not self.is_upgrade(metadata, self.csv_data[yt_title]):
                return False, {"error": "Track exists in CSV and is not an upgrade"}
        
        return True, metadata