#!/usr/bin/env python3
"""
FMP Ultimate - YouTube Music Search & Match Audit Script (Dry Run)
================================================================================
Searches YouTube Music for tracks missing URLs in the CSV database,
calculates text similarity scores and duration differences, and outputs
a draft audit CSV for user review.
"""

import os
import csv
import sys
import time
import difflib
from ytmusicapi import YTMusic

# File paths
CSV_PATH = "C:/FMP_Ultimate/configs/fmp_data_7718.csv"
OUTPUT_PATH = "C:/FMP_Ultimate/configs/missing_urls_review.csv"

def duration_to_seconds(dur_str):
    if not dur_str or dur_str == '--:--':
        return 0
    try:
        parts = list(map(int, dur_str.split(':')))
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    except Exception:
        pass
    return 0

def length_to_seconds(length_str):
    if not length_str:
        return 0
    try:
        parts = list(map(int, length_str.split(':')))
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    except Exception:
        pass
    return 0

def main():
    print("==================================================")
    print("   FMP ULTIMATE - MISSING URL SEARCH ENGINE    ")
    print("                 (Dry Run Mode)               ")
    print("==================================================")

    if not os.path.exists(CSV_PATH):
        print(f"[Error] Master CSV catalog not found at {CSV_PATH}")
        sys.exit(1)

    # 1. Parse CSV and filter tracks missing URLs
    missing_tracks = []
    total_records = 0
    
    with open(CSV_PATH, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_records += 1
            if not row.get('Source_URL') or not row.get('Source_URL').strip():
                missing_tracks.append(row)

    print(f"[*] Loaded {total_records} total catalog tracks.")
    print(f"[*] Found {len(missing_tracks)} tracks missing Source URLs.")
    print("--------------------------------------------------")

    if not missing_tracks:
        print("[OK] All tracks already have Source URLs! Exiting.")
        sys.exit(0)

    # Initialize YouTube Music API
    print("[*] Initializing YouTube Music API client...")
    try:
        ytm = YTMusic()
    except Exception as e:
        print(f"[Fatal] Failed to initialize YTMusic API: {e}")
        sys.exit(1)

    results_data = []

    # Iterate and search
    for idx, row in enumerate(missing_tracks, 1):
        track_name = row.get('Track Name', '').strip()
        file_path = row.get('File Path', '').strip()
        csv_len_str = row.get('Length', '').strip()
        csv_duration_ms = row.get('duration_ms', '0')
        
        # Calculate duration in seconds
        csv_seconds = length_to_seconds(csv_len_str)
        if csv_seconds == 0:
            try:
                csv_seconds = int(csv_duration_ms) // 1000
            except:
                pass

        safe_track_name = track_name.encode('ascii', errors='replace').decode('ascii')
        print(f"[{idx}/{len(missing_tracks)}] Searching: '{safe_track_name}'...")
        
        matched_url = ""
        matched_title = ""
        matched_artist = ""
        yt_seconds = 0
        confidence = 0
        status_flag = "No Match"

        try:
            # Strictly search using raw string
            search_results = ytm.search(track_name, filter="songs")
            if search_results and isinstance(search_results, list):
                best_match = search_results[0]
                
                # Extract details
                video_id = best_match.get('videoId')
                matched_title = best_match.get('title', '')
                artists_list = best_match.get('artists', [])
                matched_artist = ", ".join([a.get('name', '') for a in artists_list])
                dur_str = best_match.get('duration', '--:--')
                yt_seconds = duration_to_seconds(dur_str)

                if video_id:
                    matched_url = f"https://music.youtube.com/watch?v={video_id}"
                    status_flag = "Matched"
                    
                    # Compute confidence text score
                    original_clean = track_name.lower()
                    matched_full = f"{matched_artist} - {matched_title}".lower()
                    confidence = int(round(difflib.SequenceMatcher(None, original_clean, matched_full).ratio() * 100))
            else:
                status_flag = "No Results"
        except Exception as e:
            safe_err = str(e).encode('ascii', errors='replace').decode('ascii')
            print(f"  [-] Search error: {safe_err}")
            status_flag = f"Error: {e}"

        dur_diff = abs(csv_seconds - yt_seconds) if yt_seconds > 0 else 0

        # Log search resolution details
        if status_flag == "Matched":
            safe_match = f"{matched_artist} - {matched_title}".encode('ascii', errors='replace').decode('ascii')
            print(f"  [OK] Resolved to: '{safe_match}' ({dur_str})")
            print(f"      URL: {matched_url} | Confidence: {confidence}% | Dur Diff: {dur_diff}s")
        else:
            print(f"  [FAIL] Search failed: {status_flag}")

        results_data.append({
            'Track Name': track_name,
            'File Path': file_path,
            'Current Duration': csv_len_str or f"{csv_seconds // 60}:{csv_seconds % 60:02d}",
            'Matched YouTube Title': matched_title,
            'Matched YouTube Artist': matched_artist,
            'Matched YouTube URL': matched_url,
            'Matched YouTube Duration': f"{yt_seconds // 60}:{yt_seconds % 60:02d}" if yt_seconds > 0 else "--:--",
            'Duration Difference (seconds)': dur_diff,
            'Match Confidence Score (0-100)': confidence
        })

        # Small delay to respect API rate limits
        time.sleep(0.5)

    # 2. Write output CSV review spreadsheet
    print("--------------------------------------------------")
    print(f"[*] Writing review spreadsheet to {OUTPUT_PATH}...")
    
    headers = [
        'Track Name', 'File Path', 'Current Duration', 
        'Matched YouTube Title', 'Matched YouTube Artist', 
        'Matched YouTube URL', 'Matched YouTube Duration', 
        'Duration Difference (seconds)', 'Match Confidence Score (0-100)'
    ]
    
    try:
        with open(OUTPUT_PATH, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(results_data)
        print(f"[OK] Successfully wrote audit results to {OUTPUT_PATH}!")
    except Exception as e:
        print(f"[Error] Failed to write review file: {e}")
        sys.exit(1)

    print("==================================================")
    print("                 SEARCH COMPLETE                  ")
    print("==================================================")

if __name__ == "__main__":
    main()
