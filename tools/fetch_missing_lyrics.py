import os
import sys
import io
import csv
import re
import json
import time
import urllib.request
import urllib.parse
from mutagen.mp3 import MP3
from mutagen.id3 import USLT, ID3

# Reconfigure stdout/stderr to handle UTF-8 symbols and smart quotes on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

CSV_PATH = r"configs/fmp_data_7718.csv"
LYRICS_DIR = r"configs/lyrics"
API_URL_TEMPLATE = "https://api.lyrics.ovh/v1/{artist}/{title}"

def clean_term(term: str) -> str:
    """Removes trailing bracketed/parenthetical album info or explicit markers."""
    if not term:
        return ""
    # Remove text in square brackets, e.g. [Album Name] or [Cry Baby]
    term = re.sub(r'\[.*?\]', '', term)
    # Remove common tags like (mono), (stereo), (Explicit), (mono single version)
    term = re.sub(r'\((mono|stereo|explicit|single version|radio edit|remix).*?\)', '', term, flags=re.IGNORECASE)
    # Strip double spaces and trim
    return re.sub(r'\s+', ' ', term).strip()

def search_and_embed_lyrics():
    print("================================================================================")
    print("  FMP ULTIMATE - AUTO-FETCH MISSING LYRICS UTILITY")
    print("================================================================================\n")

    if not os.path.exists(CSV_PATH):
        print(f"[-] Error: Master CSV database not found at {CSV_PATH}")
        return

    os.makedirs(LYRICS_DIR, exist_ok=True)

    # 1. Read the Master CSV
    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    # 2. Identify missing tracks
    missing_tracks = []
    for index, row in enumerate(rows):
        track_name = row.get("Track Name", "").strip()
        lyrics_status = row.get("Lyrics", "").strip()
        file_path = row.get("File Path", "").strip()

        # If lyrics are not marked as True, and we have a valid track/file path
        if lyrics_status != "True" and track_name and file_path:
            missing_tracks.append((index, track_name, file_path))

    total_missing = len(missing_tracks)
    print(f"[*] Total tracks in database: {len(rows)}")
    print(f"[*] Tracks missing lyrics: {total_missing}")

    if total_missing == 0:
        print("[+] All tracks in your library already have lyrics! Nothing to do.")
        return

    from tqdm import tqdm

    print("\n[*] Starting lyrics lookup queue...")
    success_count = 0
    not_found_count = 0
    failed_requests = 0
    missing_on_disk = 0

    pbar = tqdm(missing_tracks, desc="Auto-Fetching Lyrics", unit="track")
    for index_in_db, track_name, file_path in pbar:
        norm_path = os.path.normpath(file_path)
        
        # Verify physical file exists on disk (Z: drive)
        if not os.path.exists(norm_path):
            missing_on_disk += 1
            pbar.set_postfix(found=success_count, missing_disk=missing_on_disk)
            continue

        # Extract Artist and Title
        if " - " in track_name:
            parts = track_name.split(" - ", 1)
            raw_artist = parts[0].strip()
            raw_title = parts[1].strip()
        else:
            raw_artist = "Unknown"
            raw_title = track_name.strip()

        # Sanitize search queries for higher match probability
        artist = clean_term(raw_artist)
        title = clean_term(raw_title)

        if not artist or not title or artist == "Unknown":
            continue

        pbar.write(f"\n[*] Looking up: '{artist}' - '{title}'...")
        encoded_artist = urllib.parse.quote(artist)
        encoded_title = urllib.parse.quote(title)
        url = API_URL_TEMPLATE.format(artist=encoded_artist, title=encoded_title)

        lyrics_text = ""
        try:
            req = urllib.request.Request(
                url, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            # 5-second timeout to keep the pipeline moving
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                lyrics_text = data.get("lyrics", "").strip()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                pbar.write(f"   -> [NOT FOUND] No lyrics matching this song on the server.")
                not_found_count += 1
            else:
                pbar.write(f"   -> [SERVER ERROR] Code {e.code} on request.")
                failed_requests += 1
        except Exception as e:
            pbar.write(f"   -> [FAILED] Connection error: {e}")
            failed_requests += 1

        # Standard rate-limiting sleep to prevent server blocks
        time.sleep(1.0)

        if lyrics_text:
            pbar.write(f"   -> [FOUND] Retrieved successfully! ({len(lyrics_text)} chars)")
            
            # A. Save full text file locally
            safe_track_name = "".join(c for c in track_name if c not in r'\/:*?"<>|').strip()
            txt_filepath = os.path.join(LYRICS_DIR, f"{safe_track_name}.txt")
            try:
                with open(txt_filepath, 'w', encoding='utf-8') as lyrics_file:
                    lyrics_file.write(lyrics_text)
            except Exception as e:
                pbar.write(f"   -> [WARNING] Failed to write text file: {e}")

            # B. Permanently embed into the physical MP3 ID3 tags
            try:
                audio = MP3(norm_path)
                if audio.tags is None:
                    audio.add_tags()
                
                # Write to standard Unsynchronized Lyrics (USLT) ID3 frame
                audio.tags.add(USLT(encoding=3, lang='eng', desc='Lyrics', text=lyrics_text))
                audio.save()
                pbar.write(f"   -> [EMBEDDED] Lyrics permanently written to MP3 tags.")
            except Exception as e:
                pbar.write(f"   -> [WARNING] Mutagen ID3 tagging failed: {e}")

            # C. Update cell in our memory row to "True"
            rows[index_in_db]["Lyrics"] = "True"
            success_count += 1
            pbar.set_postfix(found=success_count, not_found=not_found_count)

            # Commit database update immediately every 5 successful tags to prevent data loss on cancel
            if success_count % 5 == 0:
                write_db(rows, fieldnames)

    # 3. Final Database Write-Through
    if success_count > 0:
        write_db(rows, fieldnames)

    print("\n" + "="*80)
    print(" LYRICS RESOLUTION SUMMARY REPORT")
    print("-"*80)
    print(f"  - Total Missing Scanned:    {total_missing}")
    print(f"  - Missing physically on Z:  {missing_on_disk} (Skipped)")
    print(f"  - Successfully Tagged:      {success_count}")
    print(f"  - Verified Not Available:   {not_found_count}")
    print(f"  - Network Errors / Retries: {failed_requests}")
    print("="*80 + "\n")

def write_db(rows, fieldnames):
    try:
        with open(CSV_PATH, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    except Exception as e:
        print(f"[-] Critical: Failed to save database changes: {e}")

if __name__ == "__main__":
    search_and_embed_lyrics()
