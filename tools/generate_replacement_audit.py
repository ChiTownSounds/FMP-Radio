#!/usr/bin/env python3
"""
FMP Ultimate - Explicit Naming & Replacement Audit Script (Dry Run)
================================================================================
Queries YouTube Music to build a spreadsheet audit for the 524 URL-less tracks:
1. Recommends renaming explicit tracks (removing the explicit suffix).
2. Verifies clean tracks to catch explicit leaks.
3. Finds clean counterparts for explicit tracks.
"""

import os
import csv
import sys
import time
import re
import difflib
from ytmusicapi import YTMusic

# Append parent directory to sys.path to import project config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import is_non_song

CSV_PATH = "C:/FMP_Ultimate/configs/fmp_data_7718.csv"
OUTPUT_PATH = "C:/FMP_Ultimate/configs/replacement_audit.csv"

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

def clean_track_query(track_name):
    # Remove Explicit suffixes
    cleaned = re.sub(r'\((Explicit|Explicit Version|Dirty|Uncut)\)', '', track_name, flags=re.IGNORECASE)
    cleaned = re.sub(r'\[(Explicit|Explicit Version|Dirty|Uncut)\]', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace('_', ' ').replace('\uFFFD', ' ')
    return cleaned.strip()

def main():
    print("==================================================")
    print("      FMP ULTIMATE - REPLACEMENT AUDIT ENGINE     ")
    print("                 (Dry Run Mode)               ")
    print("==================================================")

    if not os.path.exists(CSV_PATH):
        print(f"[Error] Master CSV catalog not found at {CSV_PATH}")
        sys.exit(1)

    # 1. Parse CSV and filter URL-less tracks
    no_url_tracks = []
    with open(CSV_PATH, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get('Source_URL') or not row.get('Source_URL').strip():
                track_name = row.get('Track Name', '').strip()
                file_path = row.get('File Path', '').strip()
                item_type = row.get('item_type', '').strip().lower()
                
                # Check 1: Must be music
                if item_type not in ('music', ''):
                    continue
                # Check 2: Must pass the physical non-song path/name filters
                if is_non_song(track_name, file_path):
                    continue
                
                no_url_tracks.append(row)

    print(f"[*] Loaded {len(no_url_tracks)} tracks missing Source URLs.")
    print("--------------------------------------------------")

    if not no_url_tracks:
        print("[OK] No tracks missing URLs. Exiting.")
        sys.exit(0)

    print("[*] Initializing YouTube Music API...")
    try:
        ytm = YTMusic()
    except Exception as e:
        print(f"[Fatal] Failed to initialize YTMusic: {e}")
        sys.exit(1)

    audit_data = []

    for idx, row in enumerate(no_url_tracks, 1):
        track_name = row.get('Track Name', '').strip()
        file_path = row.get('File Path', '').strip()
        csv_len_str = row.get('Length', '').strip()
        csv_duration_ms = row.get('duration_ms', '0')
        is_db_explicit = row.get('Explicit', '').strip().lower() in ('true', '1')

        # Calculate duration in seconds
        csv_seconds = length_to_seconds(csv_len_str)
        if csv_seconds == 0:
            try:
                csv_seconds = int(csv_duration_ms) // 1000
            except:
                pass

        safe_name = track_name.encode('ascii', errors='replace').decode('ascii')
        print(f"[{idx}/{len(no_url_tracks)}] Auditing: '{safe_name}' (DB Explicit={is_db_explicit})...")

        # Set default values
        proposed_action = "No Action"
        matched_url = ""
        matched_explicit = False
        matched_title = ""
        matched_artist = ""
        confidence = 0
        clean_counterpart_url = ""
        clean_counterpart_title = ""
        
        # Suffix rename check
        needs_rename = False
        if is_db_explicit and any(suffix in track_name.lower() for suffix in ['(explicit)', '[explicit]', '(dirty)', '(uncut)']):
            needs_rename = True
            proposed_action = "Rename File (Strip Explicit)"

        try:
            # Query clean version of track name
            query_str = clean_track_query(track_name)
            
            # 1. Search for the natural/base version first
            search_results = ytm.search(query_str, filter="songs", ignore_spelling=True)
            if search_results and isinstance(search_results, list):
                best_match = search_results[0]
                matched_explicit = best_match.get('isExplicit', False)
                matched_title = best_match.get('title', '')
                artists_list = best_match.get('artists', [])
                matched_artist = ", ".join([a.get('name', '') for a in artists_list])
                video_id = best_match.get('videoId')
                if video_id:
                    matched_url = f"https://music.youtube.com/watch?v={video_id}"
                    original_clean = query_str.lower()
                    matched_full = f"{matched_artist} - {matched_title}".lower()
                    confidence = int(round(difflib.SequenceMatcher(None, original_clean, matched_full).ratio() * 100))

                # 2. Check for explicit leaks
                if not is_db_explicit and matched_explicit:
                    proposed_action = "Replace (Explicit Leak Detected!)"
                elif is_db_explicit:
                    if proposed_action == "No Action":
                        proposed_action = "Verify Explicit"
                    # Try to search for Clean version counterpart
                    clean_results = ytm.search(f"{query_str} clean", filter="songs", ignore_spelling=True)
                    if clean_results and isinstance(clean_results, list):
                        for res in clean_results[:3]:
                            if not res.get('isExplicit', False):
                                c_video_id = res.get('videoId')
                                if c_video_id:
                                    clean_counterpart_url = f"https://music.youtube.com/watch?v={c_video_id}"
                                    clean_counterpart_title = f"{res.get('title')} by {', '.join([a.get('name', '') for a in res.get('artists', [])])}"
                                    if proposed_action in ("No Action", "Verify Explicit"):
                                        proposed_action = "Fetch Clean Counterpart"
                                    elif proposed_action == "Rename File (Strip Explicit)":
                                        proposed_action = "Rename + Fetch Clean"
                                    break
            else:
                proposed_action = "Manual Check (No YTM Search Results)"

        except Exception as e:
            safe_err = str(e).encode('ascii', errors='replace').decode('ascii')
            print(f"  [-] Error: {safe_err}")
            proposed_action = f"Error: {safe_err}"

        safe_match = f"{matched_artist} - {matched_title}".encode('ascii', errors='replace').decode('ascii')
        print(f"  [Match] '{safe_match}' | YTM Explicit={matched_explicit} | Confidence={confidence}%")
        print(f"  [Action] {proposed_action}")
        if clean_counterpart_url:
            print(f"  [Counterpart] Found clean: {clean_counterpart_url}")

        audit_data.append({
            'Track Name': track_name,
            'File Path': file_path,
            'DB Explicit': is_db_explicit,
            'Proposed Action': proposed_action,
            'Matched Title': matched_title,
            'Matched Artist': matched_artist,
            'Matched YTM URL': matched_url,
            'Matched YTM Explicit': matched_explicit,
            'Match Confidence (0-100)': confidence,
            'Clean Counterpart URL': clean_counterpart_url,
            'Clean Counterpart Title': clean_counterpart_title
        })

        time.sleep(0.5)

    # 2. Write CSV
    print("--------------------------------------------------")
    print(f"[*] Writing replacement audit report to {OUTPUT_PATH}...")
    headers = [
        'Track Name', 'File Path', 'DB Explicit', 'Proposed Action',
        'Matched Title', 'Matched Artist', 'Matched YTM URL',
        'Matched YTM Explicit', 'Match Confidence (0-100)',
        'Clean Counterpart URL', 'Clean Counterpart Title'
    ]

    try:
        with open(OUTPUT_PATH, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(audit_data)
        print(f"[OK] Successfully wrote audit spreadsheet to {OUTPUT_PATH}!")
    except Exception as e:
        print(f"[Error] Failed to write CSV file: {e}")
        sys.exit(1)

    print("==================================================")
    print("                 AUDIT COMPLETE                   ")
    print("==================================================")

if __name__ == "__main__":
    main()
