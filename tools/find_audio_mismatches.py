import os
import sys
import csv
import json
import time
import argparse
import subprocess
import io
import requests
from pathlib import Path
from mutagen.mp3 import MP3
from thefuzz import fuzz
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT_DIR)
from config import CSV_BLUEPRINT, MUSIC_DIR

# Fix encoding for Windows consoles
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Load environment
load_dotenv(dotenv_path=Path(os.path.join(ROOT_DIR, ".env")))

CSV_PATH = Path(CSV_BLUEPRINT)
G_DRIVE_BASE = Path(MUSIC_DIR)
CACHE_PATH = Path(os.path.join(ROOT_DIR, "configs", "audio_fingerprint_cache.json"))
LOG_PATH = Path(os.path.join(ROOT_DIR, "logs", "audio_mismatches.txt"))
MISMATCHES_TXT_PATH = Path(os.path.join(ROOT_DIR, "logs", "mismatches.txt"))

DEFAULT_API_KEY = os.getenv("ACOUSTID_API_KEY")
# Fallback to working public/example client key if invalid or default placeholder
if not DEFAULT_API_KEY or DEFAULT_API_KEY == "cc5tCw5q9G":
    DEFAULT_API_KEY = "cSpUJKpD"

def parse_args():
    parser = argparse.ArgumentParser(description="FMP AcoustID Audio Mismatch Detector")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of tracks scanned")
    parser.add_argument("--mismatches-only", action="store_true", help="Only scan files listed in logs/mismatches.txt")
    parser.add_argument("--folder", type=str, default=None, help="Filter to scan only specific subfolder (e.g. Classics)")
    parser.add_argument("--api-key", type=str, default=DEFAULT_API_KEY, help="AcoustID API Key")
    parser.add_argument("--clear-cache", action="store_true", help="Clear the fingerprint cache before running")
    return parser.parse_args()

def load_cache():
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] Warning: Failed to load cache: {e}. Starting fresh.")
    return {}

def save_cache(cache):
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] Warning: Failed to save cache: {e}")

def get_id3_tags(file_path):
    artist = ""
    title = ""
    try:
        audio = MP3(file_path)
        if audio.tags:
            if 'TPE1' in audio.tags:
                artist = str(audio.tags['TPE1'].text[0]).strip()
            if 'TIT2' in audio.tags:
                title = str(audio.tags['TIT2'].text[0]).strip()
    except Exception:
        pass
    return artist, title

def get_audio_fingerprint(file_path, fpcalc_path=None):
    if not fpcalc_path:
        import platform
        import shutil
        if platform.system() == "Windows":
            fpcalc_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fpcalc.exe")
        else:
            fpcalc_path = shutil.which("fpcalc") or "fpcalc"
    try:
        cmd = [fpcalc_path, "-json", str(file_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return data.get("duration"), data.get("fingerprint")
    except Exception as e:
        print(f"  [!] Failed to run fpcalc on {file_path.name}: {e}")
        return None, None

def lookup_acoustid(api_key, duration, fingerprint):
    url = "https://api.acoustid.org/v2/lookup"
    params = {
        "client": api_key,
        "duration": int(duration),
        "fingerprint": fingerprint,
        "meta": "recordings"
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            res_data = response.json()
            if res_data.get("status") == "ok":
                return res_data.get("results", [])
        elif response.status_code == 400:
            res_data = response.json()
            err = res_data.get("error", {})
            print(f"  [!] AcoustID API Error: {err.get('message', 'Bad Request')}")
    except Exception as e:
        print(f"  [!] AcoustID API request failed: {e}")
    return []

def get_best_acoustid_match(results, expected_name=None):
    if not results:
        return None
    
    # Sort results by confidence score descending
    sorted_results = sorted(results, key=lambda x: x.get("score", 0.0), reverse=True)
    best_result = sorted_results[0]
    score = best_result.get("score", 0.0)
    
    recordings = best_result.get("recordings", [])
    if not recordings:
        return {"score": score, "artist": "", "title": "", "recordings": []}
    
    # Extract clean list of recordings for caching and comparison
    recordings_list = []
    for rec in recordings:
        artists = rec.get("artists", [])
        artist_name = ", ".join([a.get("name") for a in artists if a.get("name")])
        title = rec.get("title")
        if artist_name and title:
            recordings_list.append({"artist": artist_name, "title": title})

    # If we have an expected name, find the recording in the list that matches expected_name the best
    if expected_name:
        best_rec = None
        best_rec_score = -1
        for rec in recordings_list:
            full_name = f"{rec['artist']} - {rec['title']}"
            match_score = fuzz.token_set_ratio(expected_name.lower(), full_name.lower())
            if match_score > best_rec_score:
                best_rec_score = match_score
                best_rec = {"score": score, "artist": rec["artist"], "title": rec["title"], "recordings": recordings_list}
        if best_rec and best_rec_score >= 60:
            return best_rec

    # Fallback to first recording with valid artist and title
    if recordings_list:
        return {"score": score, "artist": recordings_list[0]["artist"], "title": recordings_list[0]["title"], "recordings": recordings_list}
            
    # Fallback to first recording raw
    rec = recordings[0]
    artists = rec.get("artists", [])
    artist_name = ", ".join([a.get("name") for a in artists if a.get("name")])
    fallback_match = {"score": score, "artist": artist_name or "Unknown Artist", "title": rec.get("title") or "Unknown Title", "recordings": []}
    return fallback_match

def is_audio_match(expected, actual):
    import re
    # Normalize strings
    expected = expected.lower()
    actual = actual.lower()
    
    # Remove bracketed album names like [Dangerous] from expected to avoid skewing comparisons
    expected_clean = re.sub(r'\[.*?\]', '', expected).strip()
    
    if " - " in expected_clean and " - " in actual:
        exp_parts = expected_clean.split(" - ", 1)
        act_parts = actual.split(" - ", 1)
        
        exp_artist = exp_parts[0].strip()
        exp_title = exp_parts[1].strip()
        
        act_artist = act_parts[0].strip()
        act_title = act_parts[1].strip()
        
        artist_score = fuzz.token_set_ratio(exp_artist, act_artist)
        title_score = fuzz.token_set_ratio(exp_title, act_title)
        
        # Artist needs to match well (>=70) AND title needs to match well (>=65)
        if artist_score >= 70 and title_score >= 65:
            return True, artist_score, title_score
        else:
            return False, artist_score, title_score
    else:
        # Fallback if " - " not in one of them
        full_score = fuzz.token_set_ratio(expected_clean, actual)
        return (full_score >= 60), full_score, full_score

def main():
    args = parse_args()
    
    print("=" * 70)
    print(" FMP AUDIO-BASED MISMATCH DETECTOR (ACOUSTID WAVEFORM ANALYSIS)")
    print("=" * 70)
    print(f"Using API Key: {args.api_key}")
    
    if args.clear_cache:
        print("[*] Clearing cache...")
        cache = {}
    else:
        cache = load_cache()
        print(f"[*] Loaded {len(cache)} cached fingerprints.")

    # 1. Gather target files
    tracks_to_scan = []
    
    # Optional: Read paths from mismatches.txt if --mismatches-only is specified
    mismatch_paths = set()
    if args.mismatches_only:
        if not MISMATCHES_TXT_PATH.exists():
            print(f"[FATAL] mismatches.txt not found at {MISMATCHES_TXT_PATH}")
            return
        with open(MISMATCHES_TXT_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                if "Path: " in line:
                    path_str = line.split("Path: ")[1].strip()
                    if path_str:
                        mismatch_paths.add(Path(path_str).resolve())
        print(f"[*] Loaded {len(mismatch_paths)} tracks flagged in mismatches.log")

    if not CSV_PATH.exists():
        print(f"[FATAL] CSV Database not found at {CSV_PATH}")
        return

    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            track_name = row.get("Track Name", "").strip()
            z_path = row.get("File Path", "").strip()

            if not track_name or not z_path:
                continue

            # Convert Z:/ or VM path to relative path
            clean_z = z_path.replace('\\', '/')
            if clean_z.upper().startswith("Z:/"):
                rel_path = clean_z[3:]
            elif clean_z.lower().startswith("/home/ubuntu/music/"):
                rel_path = clean_z[len("/home/ubuntu/music/"):]
            else:
                rel_path = clean_z

            g_path = G_DRIVE_BASE / rel_path
            resolved_g_path = g_path.resolve()

            # Filter by subfolder if requested
            if args.folder and args.folder.lower() not in str(rel_path).lower():
                continue

            # Filter by mismatches-only if requested
            if args.mismatches_only and resolved_g_path not in mismatch_paths:
                continue

            if not g_path.exists():
                continue

            tracks_to_scan.append((track_name, g_path))

    total_tracks = len(tracks_to_scan)
    if args.limit:
        tracks_to_scan = tracks_to_scan[:args.limit]
        print(f"[*] Scanning limited to first {len(tracks_to_scan)} tracks out of {total_tracks} total matches.")
    else:
        print(f"[*] Found {total_tracks} tracks matching filters to scan.")

    # 2. Perform the scan
    results_report = []
    scanned_count = 0
    cache_hits = 0
    api_calls = 0
    
    matches_count = 0
    tag_mismatches_count = 0
    audio_mismatches_count = 0
    unknowns_count = 0

    try:
        for expected_name, g_path in tracks_to_scan:
            scanned_count += 1
            print(f"\n[{scanned_count}/{len(tracks_to_scan)}] Scanning: {expected_name}")
            
            # Read local file properties
            mtime = g_path.stat().st_mtime
            path_str = str(g_path)
            
            id3_artist, id3_title = get_id3_tags(g_path)
            id3_full = f"{id3_artist} - {id3_title}".strip()
            if id3_full == "-":
                id3_full = ""

            # Check cache
            cached_entry = cache.get(path_str)
            if cached_entry and cached_entry.get("mtime") == mtime and ("acoustid_artist" in cached_entry or "recordings" in cached_entry):
                cache_hits += 1
                cached_recs = cached_entry.get("recordings", [])
                
                # If cached recordings list exists, run dynamic match check against expected_name
                if cached_recs and expected_name:
                    best_rec = None
                    best_rec_score = -1
                    for rec in cached_recs:
                        artist_name = rec.get("artist", "")
                        title = rec.get("title", "")
                        if artist_name and title:
                            full_name = f"{artist_name} - {title}"
                            match_score = fuzz.token_set_ratio(expected_name.lower(), full_name.lower())
                            if match_score > best_rec_score:
                                best_rec_score = match_score
                                best_rec = (artist_name, title)
                    if best_rec and best_rec_score >= 60:
                        acoustid_artist, acoustid_title = best_rec
                    else:
                        acoustid_artist = cached_entry.get("acoustid_artist", "")
                        acoustid_title = cached_entry.get("acoustid_title", "")
                else:
                    acoustid_artist = cached_entry.get("acoustid_artist", "")
                    acoustid_title = cached_entry.get("acoustid_title", "")
                    
                acoustid_score = cached_entry.get("acoustid_score", 1.0)
                print(f"  [CACHE HIT] AcoustID: {acoustid_artist} - {acoustid_title} (Score: {acoustid_score:.2f})")
            else:
                # Fingerprint
                duration, fingerprint = get_audio_fingerprint(g_path)
                if not fingerprint:
                    print("  [!] Failed to generate fingerprint.")
                    continue
                
                # API Lookup
                print("  [API] Querying AcoustID API...")
                api_calls += 1
                lookup_results = lookup_acoustid(args.api_key, duration, fingerprint)
                
                best_match = get_best_acoustid_match(lookup_results, expected_name)
                recordings_list = []
                if best_match:
                    acoustid_artist = best_match["artist"]
                    acoustid_title = best_match["title"]
                    acoustid_score = best_match["score"]
                    recordings_list = best_match.get("recordings", [])
                    print(f"  [API MATCH] AcoustID: {acoustid_artist} - {acoustid_title} (Score: {acoustid_score:.2f})")
                else:
                    acoustid_artist = ""
                    acoustid_title = ""
                    acoustid_score = 0.0
                    print("  [API Match] No results found.")

                # Save to cache
                cache[path_str] = {
                    "mtime": mtime,
                    "acoustid_artist": acoustid_artist,
                    "acoustid_title": acoustid_title,
                    "acoustid_score": acoustid_score,
                    "recordings": recordings_list
                }
                
                # Throttling
                time.sleep(0.5)

            # 3. Analyze Mismatch
            if not acoustid_artist and not acoustid_title:
                status = "UNKNOWN"
                diagnostics = "No AcoustID fingerprint found in database."
                unknowns_count += 1
            else:
                acoustid_full = f"{acoustid_artist} - {acoustid_title}"
                
                # Strict split-based audio matching
                is_match, art_score, tit_score = is_audio_match(expected_name, acoustid_full)
                
                # Check if internal ID3 tags match expected name
                id3_match, _, _ = is_audio_match(expected_name, id3_full) if id3_full else (False, 0, 0)
                
                if is_match:
                    # Audio matches the filename/CSV!
                    if id3_full and not id3_match:
                        # Audio is correct, but internal ID3 tags are completely wrong!
                        status = "TAG_MISMATCH"
                        diagnostics = f"Audio matches, but ID3 tag is wrong (ID3: '{id3_full}' vs AcoustID: '{acoustid_full}')"
                        tag_mismatches_count += 1
                    else:
                        status = "MATCH"
                        diagnostics = "Audio and ID3 tags align with expected track name."
                        matches_count += 1
                else:
                    # Audio does not match the filename/CSV!
                    status = "AUDIO_MISMATCH"
                    diagnostics = f"Expected: '{expected_name}' but Audio is actually '{acoustid_full}' (Artist score: {art_score}%, Title score: {tit_score}%)"
                    audio_mismatches_count += 1

            print(f"  Result: [{status}] {diagnostics}")
            results_report.append({
                "path": path_str,
                "expected": expected_name,
                "id3": id3_full,
                "acoustid": f"{acoustid_artist} - {acoustid_title}" if (acoustid_artist or acoustid_title) else "Unknown",
                "status": status,
                "diagnostics": diagnostics
            })

            # Save cache periodically every 10 updates
            if api_calls % 10 == 0 and api_calls > 0:
                save_cache(cache)

    except KeyboardInterrupt:
        print("\n[!] Scan interrupted by user.")
    
    # Save final cache
    save_cache(cache)

    # 4. Generate report file
    print("\n" + "=" * 70)
    print(" SCAN COMPLETE - GENERATING REPORT")
    print("=" * 70)
    print(f"Total Scanned:         {scanned_count}")
    print(f"Cache Hits:            {cache_hits}")
    print(f"API Queries:           {api_calls}")
    print("-" * 70)
    print(f"Matches [✓]:           {matches_count}")
    print(f"Tag Mismatches [✎]:    {tag_mismatches_count}")
    print(f"Audio Mismatches [✗]:  {audio_mismatches_count}")
    print(f"Unknowns [?]:          {unknowns_count}")
    print("=" * 70)

    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write(" FMP AUDIO WAVEFORM MISMATCH REPORT (ACOUSTID)\n")
            f.write("=" * 80 + "\n")
            f.write(f"Scanned: {scanned_count} | Matches: {matches_count} | Tag Mismatches: {tag_mismatches_count} | Audio Mismatches: {audio_mismatches_count} | Unknowns: {unknowns_count}\n")
            f.write("-" * 80 + "\n\n")

            if tag_mismatches_count > 0:
                f.write("--- [✎] TAG MISMATCHES (Correct audio, incorrect ID3 tags - Safe to Retag) ---\n")
                for r in results_report:
                    if r["status"] == "TAG_MISMATCH":
                        f.write(f"Expected: {r['expected']}\n")
                        f.write(f"AcoustID: {r['acoustid']}\n")
                        f.write(f"ID3 Tag:  {r['id3']}\n")
                        f.write(f"Path:     {r['path']}\n\n")

            if audio_mismatches_count > 0:
                f.write("\n--- [✗] TRUE AUDIO MISMATCHES (Audio belongs to a completely different song - Needs Redownload) ---\n")
                for r in results_report:
                    if r["status"] == "AUDIO_MISMATCH":
                        f.write(f"Expected: {r['expected']}\n")
                        f.write(f"AcoustID: {r['acoustid']}\n")
                        f.write(f"ID3 Tag:  {r['id3']}\n")
                        f.write(f"Path:     {r['path']}\n\n")

            if unknowns_count > 0:
                f.write("\n--- [?] UNKNOWN TRACKS (Not found in AcoustID database) ---\n")
                for r in results_report:
                    if r["status"] == "UNKNOWN":
                        f.write(f"Expected: {r['expected']}\n")
                        f.write(f"ID3 Tag:  {r['id3']}\n")
                        f.write(f"Path:     {r['path']}\n\n")

        print(f"\n[✓] Detailed report saved to: {LOG_PATH}")
    except Exception as e:
        print(f"[!] Failed to write report file: {e}")

if __name__ == "__main__":
    main()
