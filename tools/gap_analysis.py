import os
import sys
import csv
import json
import subprocess
import re
from typing import Tuple, Dict, Set, List

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
REPORT_FILE = os.path.join(parent_dir, "configs", "missing_tracks_report.txt")

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

def get_ytdlp_cmd() -> list:
    """Dynamically resolves how to call yt-dlp on the current host machine."""
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
        return ["yt-dlp"]
    except (subprocess.CalledProcessError, FileNotFoundError):
        scripts_path = r"C:\Users\chito\AppData\Local\Programs\Python\Python313\Scripts\yt-dlp.exe"
        if os.path.exists(scripts_path):
            return [scripts_path]
        return [sys.executable, "-m", "yt_dlp"]

def check_validation(entry: dict) -> Tuple[bool, str]:
    """
    Validates entry metadata to filter out non-music video rips.
    """
    title = entry.get('title', '').lower()
    uploader = entry.get('uploader', '').lower()
    duration = entry.get('duration')
    
    # 1. Skip non-music keywords in title
    invalid_keywords = [
        'vlog', 'reaction', 'unboxing', 'review', 'tutorial', 
        'interview', 'podcast', 'episode', 'behind the scenes', 
        'making of', 'full movie'
    ]
    for kw in invalid_keywords:
        if kw in title:
            return False, f"Non-music keyword '{kw}' found in title"
            
    # 2. Skip excessively long videos (e.g. > 10 minutes)
    if duration and duration > 600:
        return False, f"Duration is too long ({duration}s > 600s)"
        
    # 3. Skip extremely short videos (e.g. < 45 seconds)
    if duration and duration < 45:
        return False, f"Duration is too short ({duration}s < 45s)"
        
    # 4. Check if the domain is restricted or if it is video-only format
    # Non-music uploader check (e.g., if uploader is not music-focused or contains video-oriented tags)
    video_uploaders = ['funny', 'gaming', 'review', 'vlogs', 'news', 'comedy']
    for kw in video_uploaders:
        if kw in uploader:
            return False, f"Non-music uploader keyword '{kw}'"

    return True, "Valid track"

def main():
    print("====================================================")
    print("   FMP ULTIMATE: GAP ANALYSIS SCANNER               ")
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
                    # Strip bracketed album suffix [Album]
                    track_name_clean = re.sub(r'\s*\[.*?\]\s*$', '', track_name).strip()
                    
                    if " - " in track_name_clean:
                        parts = track_name_clean.split(" - ", 1)
                        artist = parts[0].strip()
                        title = parts[1].strip()
                        # standard (artist + title).lower()
                        fingerprint = (artist + title).lower()
                        fingerprint = re.sub(r'[^a-z0-9]', '', fingerprint)
                        if fingerprint:
                            known_tracks.add(fingerprint)
                    else:
                        fingerprint = re.sub(r'[^a-z0-9]', '', track_name_clean.lower())
                        if fingerprint:
                            known_tracks.add(fingerprint)
                            
        print(f"[+] Loaded {len(known_tracks)} unique track fingerprints from master library.")
    else:
        print(f"[!] Warning: Master CSV not found at {CSV_BLUEPRINT}.")
        return

    # 2. Extract Playlist Metadata via yt-dlp
    print(f"[*] Extracting playlist metadata from: {PLAYLIST_URL}")
    ytdlp = get_ytdlp_cmd()
    cmd = ytdlp + [
        "--cookies-from-browser", "firefox",
        "--flat-playlist",
        "-j",
        PLAYLIST_URL
    ]
    
    print(f"[*] Running extraction command...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='ignore')
    except subprocess.CalledProcessError as e:
        print(f"[-] Critical Error: yt-dlp extraction failed with exit code {e.returncode}")
        print(f"[-] Stderr: {e.stderr}")
        return
    except Exception as e:
        print(f"[-] Critical Error: Subprocess failed to execute: {e}")
        return

    # 3. Categorize Playlist Tracks
    bin_a = []  # Already Owned
    bin_b = []  # Validation Rejected
    bin_c = []  # Dead/Inaccessible
    bin_valid_queued = []  # Valid and Ingested/Queued

    processed_playlist_keys = set()

    for line in result.stdout.splitlines():
        line = line.strip()
        if not (line.startswith('{') and line.endswith('}')):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        title = entry.get('title', '').strip()
        uploader = (entry.get('uploader') or entry.get('artist') or '').strip()
        url = entry.get('url') or entry.get('webpage_url')
        if not url and entry.get('id'):
            url = f"https://music.youtube.com/watch?v={entry['id']}"

        # BIN C: Dead / Inaccessible Checks
        is_dead = False
        dead_reason = ""
        if not url:
            is_dead = True
            dead_reason = "Missing URL"
        elif not title:
            is_dead = True
            dead_reason = "Missing Title (Possible private/deleted video)"
        elif title in ['[Deleted video]', '[Private video]']:
            is_dead = True
            dead_reason = f"Unavailable Video ({title})"
        
        if is_dead:
            display_name = title or f"Video ID: {entry.get('id', 'Unknown')}"
            bin_c.append({
                "artist": uploader or "Unknown Artist",
                "title": display_name,
                "url": url or "",
                "reason": dead_reason
            })
            continue

        # Create fingerprints for matching
        key_title = re.sub(r'[^a-z0-9]', '', title.lower())
        key_uploader_title = re.sub(r'[^a-z0-9]', '', (uploader + title).lower())
        key_title_uploader = re.sub(r'[^a-z0-9]', '', (title + uploader).lower())

        # BIN A: Already Owned Checks
        is_duplicate = False
        for key in [key_uploader_title, key_title_uploader, key_title]:
            if key and key in known_tracks:
                is_duplicate = True
                break
                
        if is_duplicate:
            bin_a.append({
                "artist": uploader or "Unknown Artist",
                "title": title,
                "url": url
            })
            continue

        # BIN B: Validation Checks
        is_valid, reject_reason = check_validation(entry)
        if not is_valid:
            bin_b.append({
                "artist": uploader or "Unknown Artist",
                "title": title,
                "url": url,
                "reason": reject_reason
            })
            continue

        # Prevent internal duplicates in the playlist from registering as new multiple times
        internal_key = key_uploader_title
        if internal_key in processed_playlist_keys:
            bin_a.append({
                "artist": uploader or "Unknown Artist",
                "title": title,
                "url": url,
                "reason": "Duplicate within Playlist"
            })
            continue
            
        processed_playlist_keys.add(internal_key)

        # Remaining items represent the successfully queued/ingested tracks
        bin_valid_queued.append({
            "artist": uploader or "Unknown Artist",
            "title": title,
            "url": url
        })

    # 4. Generate the missing tracks report file
    os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
    with open(REPORT_FILE, mode='w', encoding='utf-8') as f:
        f.write("========================================================================\n")
        f.write("             FMP RADIO AUTOMATION: PLAYLIST GAP ANALYSIS REPORT         \n")
        f.write("========================================================================\n\n")
        
        f.write(f"Source Playlist: {PLAYLIST_URL}\n")
        f.write(f"Total Tracks Processed: {len(bin_a) + len(bin_b) + len(bin_c) + len(bin_valid_queued)}\n")
        f.write(f"Successfully Ingested / Queued: {len(bin_valid_queued)}\n")
        f.write(f"Total Left Off / Omitted: {len(bin_a) + len(bin_b) + len(bin_c)}\n\n")
        
        f.write("------------------------------------------------------------------------\n")
        f.write(f" [BIN A: Already Owned / Duplicate in Library] - Count: {len(bin_a)}\n")
        f.write("------------------------------------------------------------------------\n")
        if bin_a:
            for idx, item in enumerate(bin_a, 1):
                f.write(f"{idx:03d}. {item['artist']} - {item['title']} (URL: {item['url']})\n")
        else:
            f.write("No tracks in this bin.\n")
        f.write("\n")
        
        f.write("------------------------------------------------------------------------\n")
        f.write(f" [BIN B: Validation Rejected / Non-Music / Video Only] - Count: {len(bin_b)}\n")
        f.write("------------------------------------------------------------------------\n")
        if bin_b:
            for idx, item in enumerate(bin_b, 1):
                f.write(f"{idx:03d}. {item['artist']} - {item['title']} | Reason: {item['reason']} (URL: {item['url']})\n")
        else:
            f.write("No tracks in this bin.\n")
        f.write("\n")
        
        f.write("------------------------------------------------------------------------\n")
        f.write(f" [BIN C: Dead / Inaccessible / Missing Metadata] - Count: {len(bin_c)}\n")
        f.write("------------------------------------------------------------------------\n")
        if bin_c:
            for idx, item in enumerate(bin_c, 1):
                f.write(f"{idx:03d}. {item['artist']} - {item['title']} | Reason: {item['reason']} (URL: {item['url']})\n")
        else:
            f.write("No tracks in this bin.\n")
        f.write("\n")

    # 5. Output summary table to terminal
    print("\n" + "="*60)
    print("               GAP ANALYSIS SUMMARY TABLE")
    print("="*60)
    print(f" {'Category / Bin':<35} | {'Track Count':^18}")
    print("-" * 60)
    print(f" [VALID QUEUED] Ingested / Queued      | {len(bin_valid_queued):^18}")
    print(f" [BIN A] Already Owned / Duplicate    | {len(bin_a):^18}")
    print(f" [BIN B] Validation Rejected          | {len(bin_b):^18}")
    print(f" [BIN C] Dead / Inaccessible          | {len(bin_c):^18}")
    print("-" * 60)
    total_processed = len(bin_a) + len(bin_b) + len(bin_c) + len(bin_valid_queued)
    print(f" {'TOTAL PROCESSED':<35} | {total_processed:^18}")
    print("="*60)
    print(f"[*] Detailed report written to: {REPORT_FILE}\n")

if __name__ == "__main__":
    main()
