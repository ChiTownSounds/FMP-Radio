import os
import re
import json
import logging
import subprocess
import requests
from typing import Tuple, Dict
from mutagen.mp3 import MP3

class AutoMaster:
    def __init__(self):
        # Target vocal frequencies: 1kHz to 3kHz range
        self.vocal_min_freq = 1000
        self.vocal_max_freq = 3000

    def _clean_lucene_string(self, text: str) -> str:
        """
        Strips metadata noise (brackets, parentheses, feat. markers) 
        and removes Lucene reserved characters to prevent API search query failures.
        """
        text = re.sub(r'\[.*?\]', '', text)
        text = re.sub(r'\(.*?\)', '', text)
        text = re.sub(r'(?i)\b(feat|ft|featuring|official|video|audio|hq|remix)\b.*', '', text)
        text = re.sub(r'[^a-zA-Z0-9\s\-\']', '', text)
        return " ".join(text.split()).strip()

    def _fetch_true_year(self, artist: str, title: str) -> str:
        """Queries the MusicBrainz API for the earliest release group year."""
        clean_artist = self._clean_lucene_string(artist)
        clean_title = self._clean_lucene_string(title)

        if not clean_artist or not clean_title:
            return "Verify Year"

        headers = {
            'User-Agent': 'FMPUltimateIngestionEngine/4.0.0 ( william.d.mckinney@gmail.com )'
        }
        url = "https://musicbrainz.org/ws/v2/recording/"
        search_query = f'artist:"{clean_artist}" AND recording:"{clean_title}"'
        params = {
            'query': search_query,
            'fmt': 'json',
            'limit': 5
        }

        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                recordings = data.get('recordings', [])
                if not recordings:
                    return "Verify Year"

                years = []
                for rec in recordings:
                    rg_list = rec.get('release-groups', [])
                    for rg in rg_list:
                        date_str = rg.get('first-release-date', '')
                        if date_str and len(date_str) >= 4:
                            match = re.match(r'^(\d{4})', date_str)
                            if match: years.append(int(match.group(1)))

                    rel_list = rec.get('releases', [])
                    for rel in rel_list:
                        date_str = rel.get('date', '')
                        if date_str and len(date_str) >= 4:
                            match = re.match(r'^(\d{4})', date_str)
                            if match: years.append(int(match.group(1)))

                if years:
                    return str(min(years)) 
            return "Verify Year"
        except Exception as e:
            logging.error(f"MusicBrainz API lookup failure: {e}")
            return "Verify Year"

    def _verify_quality(self, file_path: str) -> bool:
        """Analyzes audio channel and sample-rate baselines before vaulting."""
        try:
            cmd = [
                'ffprobe', '-v', 'error', '-select_streams', 'a:0',
                '-show_entries', 'stream=channels,sample_rate', '-of', 'json', file_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            stream = data.get('streams', [{}])[0]

            channels = int(stream.get('channels', 0))
            sample_rate = int(stream.get('sample_rate', 0))

            if channels < 2 or sample_rate < 44100:
                logging.error(f"Hard Reject: Quality threshold failed (Channels: {channels}, Sample Rate: {sample_rate}Hz)")
                if os.path.exists(file_path):
                    os.remove(file_path)
                return False
            return True
        except Exception as e:
            logging.error(f"Quality verification execution crash: {e}")
            if os.path.exists(file_path):
                os.remove(file_path)
            return False

    def _analyze_audio_properties(self, file_path: str) -> Dict:
        """Extracts Cue points via FFmpeg silence detection."""
        analysis = {'bpm': 98, 'intro_sec': 0.0, 'cue_in': 0.0, 'cue_out': 0.0}
        silence_cmd = [
            'ffmpeg', '-i', file_path, '-af', 'silencedetect=noise=-50dB:d=0.5', 
            '-f', 'null', '-'
        ]
        try:
            result = subprocess.run(silence_cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
            output = result.stderr
            end_matches = re.findall(r'silence_end:\s+([-\d.]+)', output)
            if end_matches: analysis['cue_in'] = round(float(end_matches[0]), 2)
            start_matches = re.findall(r'silence_start:\s+([-\d.]+)', output)
            if start_matches: analysis['cue_out'] = round(float(start_matches[-1]), 2)
        except Exception as e:
            logging.error(f"FFmpeg silence analysis routine failed: {e}")
        
        analysis['intro_sec'] = 12.5 
        return analysis

    def process_file(self, file_path: str, original_bitrate: str = "320k") -> Tuple[str, Dict]:
        """
        Main entry point for the AutoMaster module.
        Harvests embedded ID3 tags directly from the physical file to catch SomeDL data.
        """
        if not self._verify_quality(file_path):
            return "", {}

        clean_name = os.path.basename(file_path).replace('.mp3', '')
        
        if " - " in clean_name:
            parts = clean_name.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        else:
            artist = "Unknown Artist"
            title = clean_name.strip()

        metrics = self._analyze_audio_properties(file_path)

        # HARVEST EMBEDDED METADATA WRITTEN BY SOMEDL
        embedded_year = ""
        embedded_url = ""
        try:
            audio = MP3(file_path)
            if audio and audio.tags:
                # Extract year tags (TDRC or TYER)
                if 'TDRC' in audio.tags:
                    embedded_year = str(audio.tags['TDRC'].text[0])
                elif 'TYER' in audio.tags:
                    embedded_year = str(audio.tags['TYER'].text[0])
                
                # Extract source URL tags (yt-dlp typical mappings)
                if 'WXXX' in audio.tags:
                    embedded_url = str(audio.tags['WXXX'].url)
                else:
                    for key in audio.tags.keys():
                        if key.startswith('COMM'):
                            comment_text = str(audio.tags[key].text[0])
                            if "http" in comment_text:
                                embedded_url = comment_text
                                break
        except Exception as e:
            logging.error(f"Failed to extract embedded ID3 metadata: {e}")

        # 1. Run primary deep historical search
        true_year = self._fetch_true_year(artist, title)

        # 2. RECONCILIATION FALLBACK: If web search fails, apply the embedded tag from SomeDL
        if not true_year or true_year == "Verify Year":
            if embedded_year:
                year_match = re.search(r'(\d{4})', str(embedded_year))
                if year_match:
                    true_year = year_match.group(1)

        # Final safety check to protect database row consistency
        if not true_year or str(true_year).strip() == "" or true_year == "Verify Year":
            true_year = "Unknown"

        metadata_updates = {
            'bitrate': original_bitrate,
            'lyrics': 'Not Found',
            'art_ratio': '1.0',
            'release_year': true_year,
            'bpm': metrics['bpm'],
            'intro_sec': metrics['intro_sec'],
            'cue_in': metrics['cue_in'],
            'cue_out': metrics['cue_out']
        }
        
        # Only overwrite the URL if SomeDL successfully embedded one.
        # This protects the yt-dlp URL captured earlier by Gatekeeper.
        if embedded_url and embedded_url.strip():
            metadata_updates['url'] = embedded_url.strip()

        return file_path, metadata_updates