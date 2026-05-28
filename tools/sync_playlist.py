import os
import sys
import csv
import json
import subprocess
import re
from typing import Tuple, Dict, Set

# Add project root to path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from config import CSV_BLUEPRINT

# Set output to use UTF-8 on Windows
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

PLAYLIST_URL = "https://music.youtube.com/playlist?list=PLnwUcXVs1Id3zLSZtIUV3gx6gGRWTV-GI"
QUEUE_FILE = os.path.join(parent_dir, "configs", "playlist_queue.txt")

def normalize_for_match(text: str) -> str:
    """Strips brackets, parentheses, non-alphanumeric symbols and spacing to ensure bulletproof matches."""
    if not text:
        return ""
    # Strip album/bracket names [Album]
    text = re.sub(r'\[.*?\]', '', text)
    # Strip parentheses (Version/Feat)
    text = re.sub(r'\(.*?\)', '', text)
    # Lowercase
    text = text.lower()
    # Strip everything except lowercase letters and numbers
    text = re.sub(r'[^a-z0-9]', '', text)
    return text.strip()

def process_and_filter_url(entry: dict) -> Tuple[bool, str]:
    """
    Validates entry metadata to filter out non-music video rips.
    Returns:
        is_valid: bool
        reason: str
    """
    title = entry.get('title', '').lower()
    uploader = entry.get('uploader', '').lower()
    duration = entry.get('duration')
    
    # 1. Skip non-music keywords in title
    invalid_keywords = [
        'vlog', 'reaction', 'unboxing', 'review', 'tutorial', 
        'interview', 'podcast', 'episode', 'behind the scenes', 
        'making of', 'full movie', 'unboxing'
    ]
    for kw in invalid_keywords:
        if kw in title:
            return False, f"Non-music keyword '{kw}' found in title"
            
    # 2. Skip excessively long videos (e.g. > 10 minutes / 600 seconds)
    if duration and duration > 600:
        return False, f"Duration is too long ({duration}s > 600s)"
        
    # 3. Skip extremely short videos (e.g. < 45 seconds) which are likely sound effects
    if duration and duration < 45:
        return False, f"Duration is too short ({duration}s < 45s)"
        
    return True, "Valid track"

def get_ytdlp_cmd() -> list:
    """Dynamically resolves how to call yt-dlp on the current host machine."""
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
        return ["yt-dlp"]
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback 1: Standard Python Program Scripts
        scripts_path = r"C:\Users\chito\AppData\Local\Programs\Python\Python313\Scripts\yt-dlp.exe"
        if os.path.exists(scripts_path):
            return [scripts_path]
        # Fallback 2: Execute via current python interpreter module
        return [sys.executable, "-m", "yt_dlp"]

def main():
    print("====================================================")
    print("   FMP ULTIMATE: ATOMIC PLAYLIST SYNCHRONIZER       ")
    print("====================================================")
    
    # 1. Load Known Library from Master CSV
    known_tracks = set()
    print(f"[*] Reading master library metadata from: {CSV_BLUEPRINT}")
    if os.path.exists(CSV_BLUEPRINT):
        with open(CSV_BLUEPRINT, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                track_name = row.get('Track Name') or row.get('track name') or ""
                if track_name:
                    cleaned_key = normalize_for_match(track_name)
                    if cleaned_key:
                        known_tracks.add(cleaned_key)
        print(f"[+] Loaded {len(known_tracks)} unique track fingerprints from master library.")
    else:
        print(f"[!] Warning: Master CSV not found at {CSV_BLUEPRINT}. Initializing clean sync.")

    # 2. Flatten Playlist Metadata via yt-dlp
    print(f"[*] Extracting playlist metadata from: {PLAYLIST_URL}")
    ytdlp = get_ytdlp_cmd()
    cmd = ytdlp + [
        "--cookies-from-browser", "firefox",
        "--flat-playlist",
        "-j",
        PLAYLIST_URL
    ]
    
    print(f"[*] Running extraction command: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='ignore')
    except subprocess.CalledProcessError as e:
        print(f"[-] Critical Error: yt-dlp extraction failed with exit code {e.returncode}")
        print(f"[-] Stderr: {e.stderr}")
        return
    except Exception as e:
        print(f"[-] Critical Error: Subprocess failed to execute: {e}")
        return

    # 3. Parse and Process Playlist Tracks
    matched_count = 0
    new_queued_count = 0
    filtered_count = 0
    new_urls = []
    
    # Track items processed in this playlist run to prevent internal playlist duplicates
    processed_playlist_keys = set()

    for line in result.stdout.splitlines():
        line = line.strip()
        if not (line.startswith('{') and line.endswith('}')):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        title = entry.get('title', 'Unknown Title')
        uploader = entry.get('uploader', 'Unknown Artist')
        url = entry.get('url') or entry.get('webpage_url')
        if not url and entry.get('id'):
            url = f"https://music.youtube.com/watch?v={entry['id']}"

        if not url:
            print(f"[-] Skip: Entry '{title}' has no valid URL")
            filtered_count += 1
            continue

        # A. Filter Gatekeeping Checks (duration, type)
        is_valid, filter_reason = process_and_filter_url(entry)
        if not is_valid:
            print(f"[-] Filtered Video/Invalid URL: '{title}' ({filter_reason})")
            filtered_count += 1
            continue

        # B. Atomic Sync Matching Checks
        # Combine different normalization permutations to protect against naming layout shifts
        key_title = normalize_for_match(title)
        key_uploader_title = normalize_for_match(f"{uploader} - {title}")
        key_title_uploader = normalize_for_match(f"{title} - {uploader}")

        is_duplicate = False
        for key in [key_title, key_uploader_title, key_title_uploader]:
            if key and (key in known_tracks or key in processed_playlist_keys):
                is_duplicate = True
                break

        if is_duplicate:
            print(f"[-] Skip: Track exists -> '{uploader} - {title}'")
            matched_count += 1
        else:
            print(f"[+] Queued: New Unique -> '{uploader} - {title}'")
            # Register key to prevent duplicating the same song if it appears twice in the playlist
            if key_uploader_title:
                processed_playlist_keys.add(key_uploader_title)
            new_urls.append(url)
            new_queued_count += 1

    # 4. Save and Persist Queue to File
    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
    with open(QUEUE_FILE, mode='w', encoding='utf-8') as f:
        for url in new_urls:
            f.write(f"{url}\n")

    # 5. Output Verification Summaries to Terminal
    print("\n" + "="*50)
    print("   SYNC PLAYLIST COMPLETE SUMMARY")
    print("="*50)
    print(f"Existing Library tracks matched: {matched_count}")
    print(f"New unique tracks queued for download: {new_queued_count}")
    print(f"Video/Invalid URLs filtered: {filtered_count}")
    print(f"Queue written to: {QUEUE_FILE}")
    print("="*50 + "\n")

if __name__ == "__main__":
    main()
