#!/usr/bin/env python3
"""
FMP Ultimate - Queue Replacement Downloads from Audit
================================================================================
Reads configs/replacement_audit.csv:
1. Resolves clean counterpart URLs for 'Replace (Explicit Leak Detected!)' rows via YT Music.
2. Triggers `/add` endpoint on remote FMP Ultimate (https://ultimate.fmpmediagroup.com/add)
   with overwrite=True to download clean tracks and automatically overwrite duplicates.
"""

import os
import csv
import sys
import time
import re
import urllib.request
import urllib.parse
import json
import ssl
import base64
import argparse
from ytmusicapi import YTMusic

# Reconfigure stdout/stderr to UTF-8 for Windows background task runner
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

AUDIT_CSV = "C:/FMP_Ultimate/configs/replacement_audit.csv"
ADD_URL = "https://ultimate.fmpmediagroup.com/add"

# This script previously had no safety gate at all - it unconditionally
# looped over every matching row and POSTed to /add with overwrite=True.
# Defaults to a dry run; pass --live to actually queue downloads.
DRY_RUN = True

# Bypass SSL validation for remote ultimate API
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

# nginx in front of ultimate.fmpmediagroup.com requires Basic Auth on every
# path - this request was never sending it, so /add has always 401'd here
# and this tool has never actually queued a single download.
_AUTH_B64 = base64.b64encode(b"fmpadmin:773312").decode()

def clean_track_query(track_name):
    cleaned = re.sub(r'\((Explicit|Explicit Version|Dirty|Uncut)\)', '', track_name, flags=re.IGNORECASE)
    cleaned = re.sub(r'\[(Explicit|Explicit Version|Dirty|Uncut)\]', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace('_', ' ').replace('\uFFFD', ' ')
    return cleaned.strip()

def search_clean_version(ytm, track_name):
    query_str = clean_track_query(track_name)
    search_queries = [
        f"{query_str} clean",
        f"{query_str} radio edit",
        query_str
    ]
    
    # Extract lowercase words from original query for validation (excluding common helpers)
    query_words = set(re.findall(r'\w+', query_str.lower())) - {'clean', 'explicit', 'radio', 'edit', 'feat', 'with', 'remix', 'version', 'single', 'album', 're-record', 'rerecord', 'drop', 'tapconnect', 'fmp'}
    
    for query in search_queries:
        try:
            print(f"  Searching YTM: '{query}'...")
            results = ytm.search(query, filter="songs", ignore_spelling=True)
            if results and isinstance(results, list):
                for res in results[:5]:
                    if not res.get('isExplicit', False):
                        video_id = res.get('videoId')
                        if video_id:
                            title = res.get('title')
                            artist_str = ", ".join([a.get('name', '') for a in res.get('artists', [])])
                            
                            # Safety check: ensure at least one core query word is in the result
                            result_text = (title + " " + artist_str).lower()
                            result_words = set(re.findall(r'\w+', result_text))
                            
                            overlap = query_words.intersection(result_words)
                            if not overlap and query_words: # only validate if there are query words left
                                print(f"    [Skipped Mismatch] Result '{title}' by '{artist_str}' does not match query '{query_str}'.")
                                continue
                                
                            url = f"https://music.youtube.com/watch?v={video_id}"
                            print(f"    Found clean: '{title}' by '{artist_str}' -> {url}")
                            return url, f"{title} by {artist_str}"
        except Exception as e:
            print(f"    Error searching '{query}': {e}")
        time.sleep(1)
    return None, None

def trigger_download(url, target, track_name):
    payload = {
        "urls": url,
        "target": target,
        "auto_linked": True,
        "explicit": False,
        "overwrite": True
    }
    
    try:
        req = urllib.request.Request(
            ADD_URL,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Basic {_AUTH_B64}',
                'Host': 'ultimate.fmpmediagroup.com'
            },
            method='POST'
        )
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=30) as res:
            res_data = json.loads(res.read().decode('utf-8'))
            if res_data.get("status") == "ok":
                print(f"  [OK QUEUED] Enqueued: '{track_name}' -> {url}")
                return True
            else:
                print(f"  [-] Failed queue response: {res_data}")
    except Exception as e:
        print(f"  [-] Downloader communication error: {e}")
    return False

def main():
    parser = argparse.ArgumentParser(description="Queue replacement downloads")
    parser.add_argument("--start-index", type=int, default=1, help="1-indexed target number to start from")
    parser.add_argument("--live", action="store_true", help="Actually queue downloads and write the CSV (default is a dry run)")
    args = parser.parse_args()
    if args.live:
        global DRY_RUN
        DRY_RUN = False

    print("==================================================")
    print("    FMP ULTIMATE - QUEUE AUDIT REPLACEMENTS       ")
    print(f"               Start Index: {args.start_index}                   ")
    print(f"               DRY_RUN: {DRY_RUN}                   ")
    print("==================================================")

    if not os.path.exists(AUDIT_CSV):
        print(f"[Error] Audit CSV not found at {AUDIT_CSV}")
        sys.exit(1)

    print("[*] Initializing YouTube Music API...")
    try:
        ytm = YTMusic()
    except Exception as e:
        print(f"[Fatal] Failed to initialize YTMusic: {e}")
        sys.exit(1)

    # Read CSV rows
    rows = []
    fieldnames = []
    with open(AUDIT_CSV, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    # Filter rows requiring action
    targets = []
    for r in rows:
        action = r.get('Proposed Action', '')
        if action in ('Rename + Fetch Clean', 'Fetch Clean Counterpart', 'Replace (Explicit Leak Detected!)'):
            targets.append(r)

    print(f"[*] Found {len(targets)} replacement targets in the audit report.")
    print("--------------------------------------------------")

    success_count = 0
    updated_rows = list(rows)

    for idx, r in enumerate(targets, 1):
        if idx < args.start_index:
            continue

        track_name = r['Track Name']
        file_path = r['File Path']
        action = r['Proposed Action']
        clean_url = r.get('Clean Counterpart URL', '').strip()
        clean_title = r.get('Clean Counterpart Title', '').strip()

        print(f"\n[{idx}/{len(targets)}] Processing '{track_name}' (Action: {action})...")

        # Resolve target subfolder from file path
        # e.g., 'Music/Classics/Song.mp3' -> 'Classics'
        # e.g., 'Music/Song.mp3' -> ''
        path_clean = file_path.replace('\\', '/')
        path_parts = path_clean.split('/')
        target_folder = ""
        if len(path_parts) >= 3:
            target_folder = "/".join(path_parts[1:-1])

        # If it's a leak and counterpart URL is empty, search YTM
        if action == 'Replace (Explicit Leak Detected!)' and not clean_url:
            print("  [*] Search clean counterpart for explicit leak...")
            url, title = search_clean_version(ytm, track_name)
            if url:
                clean_url = url
                clean_title = title
                # Update row data in-memory
                r['Clean Counterpart URL'] = url
                r['Clean Counterpart Title'] = title
            else:
                print("  [-] Could not find clean counterpart for explicit leak. Skipping.")
                continue

        if not clean_url:
            print("  [-] No clean counterpart URL available. Skipping.")
            continue

        # Trigger download
        if DRY_RUN:
            print(f"  [DRY-RUN] Would enqueue: '{track_name}' -> {clean_url} (target: '{target_folder}')")
            success_count += 1
        else:
            success = trigger_download(clean_url, target_folder, track_name)
            if success:
                success_count += 1
        
        # Throttling to prevent API blocking
        time.sleep(2)

    # Save updated CSV back to preserve Resolved Clean URLs
    if DRY_RUN:
        print("\n[DRY-RUN] Would update configs/replacement_audit.csv with resolved URLs. Pass --live to actually apply this.")
    else:
        try:
            with open(AUDIT_CSV, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(updated_rows)
            print("\n[OK] Successfully updated configs/replacement_audit.csv with resolved URLs!")
        except Exception as e:
            print(f"\n[Warning] Failed to write updated CSV: {e}")

    print("==================================================")
    print(f"      QUEUEING COMPLETE ({success_count} enqueued)     ")
    print("==================================================")

if __name__ == "__main__":
    main()
