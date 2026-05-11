import os
import yt_dlp
import logging
from typing import Tuple, Optional
from config import STAGING_DIR

class Transporter:
    def __init__(self):
        if not os.path.exists(STAGING_DIR):
            os.makedirs(STAGING_DIR)
        
        self.cookie_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cookies.txt')

    def download_track(self, url: str) -> Tuple[Optional[str], int]:
        peek_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
        }
        if os.path.exists(self.cookie_path):
            peek_opts['cookiefile'] = self.cookie_path

        try:
            with yt_dlp.YoutubeDL(peek_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                source_abr = info.get('abr') or info.get('tbr') or 128
                
                if source_abr < 128:
                    print(f"\n[REJECTED] Source audio too weak ({source_abr}kbps). Find a better link.")
                    return None, 0
                
                if source_abr >= 250:
                    honest_bitrate = '320'
                else:
                    honest_bitrate = '192'
                    
        except Exception as e:
            logging.error(f"Transporter probe error: {e}")
            return None, 0

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(STAGING_DIR, '%(id)s.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': honest_bitrate, 
            }],
            'quiet': True,
            'no_warnings': True,
        }
        
        if os.path.exists(self.cookie_path):
            ydl_opts['cookiefile'] = self.cookie_path
            
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                final_info = ydl.extract_info(url, download=True)
                expected_path = os.path.join(STAGING_DIR, f"{final_info['id']}.mp3")
                
                if os.path.exists(expected_path):
                    # We now return the path AND the final calculated bitrate
                    return expected_path, int(honest_bitrate)
        except Exception as e:
            logging.error(f"Transporter download error: {e}")
            
        return None, 0