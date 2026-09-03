import os
import sys
import csv
import re
import urllib.request
import json
import codecs
import time

sys.stdout.reconfigure(encoding='utf-8')

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import is_non_song, CSV_BLUEPRINT
CSV_PATH = CSV_BLUEPRINT

def get_html_with_headers(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.read().decode('utf-8', errors='ignore')
    except urllib.error.HTTPError as e:
        if e.code in [403, 429]:
            raise e
        print(f"HTTP Error fetching {url}: {e.code} - {e.reason}")
        return None
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def find_nodes(node, key):
    results = []
    if isinstance(node, dict):
        if key in node:
            results.append(node[key])
        for k, v in node.items():
            results.extend(find_nodes(v, key))
    elif isinstance(node, list):
        for item in node:
            results.extend(find_nodes(item, key))
    return results

def audit_album_page(album_url, target_title=None):
    """Scrapes a YouTube Music album playlist page to get track explicit statuses and counterparts."""
    html = get_html_with_headers(album_url)
    if not html:
        return None, None, None, None

    # Parse initialData
    scripts = re.findall(r'<script.*?>.*?</script>', html, re.DOTALL)
    data_script = None
    for script in scripts:
        if 'initialData' in script and ('MUSIC_EXPLICIT_BADGE' in script or 'musicResponsiveListItemRenderer' in script):
            data_script = script
            break
            
    if not data_script:
        return None, None, None, None
        
    matches = re.findall(r"data:\s*'([\s\S]*?)'", data_script)
    if len(matches) < 2:
        return None, None, None, None
        
    try:
        decoded = matches[1].encode('utf-8').decode('unicode_escape')
        data = json.loads(decoded)
    except Exception as e:
        print(f"JSON decode failed: {e}")
        return None, None, None, None

    # Extract explicit statuses
    items = find_nodes(data, 'musicResponsiveListItemRenderer')
    statuses = []
    found_specific_explicit = None
    best_match_score = -1
    
    for item in items:
        title = "Unknown"
        try:
            title = item['flexColumns'][0]['musicResponsiveListItemFlexColumnRenderer']['text']['runs'][0]['text']
        except:
            pass
            
        is_explicit = False
        badges = item.get('badges', [])
        for badge in badges:
            try:
                badge_type = badge['musicInlineBadgeRenderer']['icon']['iconType']
                if badge_type == 'MUSIC_EXPLICIT_BADGE':
                    is_explicit = True
            except:
                pass
        statuses.append(is_explicit)
        
        # If target_title is provided, check match score
        if target_title:
            clean_item = re.sub(r'[^a-z0-9]', '', title.lower())
            clean_target = re.sub(r'[^a-z0-9]', '', target_title.lower())
            
            # Strip common suffixes/versions to find the core title
            core_item = re.sub(r'(feat|with|remix|mono|single|version|radio|edit|album).*', '', clean_item)
            core_target = re.sub(r'(feat|with|remix|mono|single|version|radio|edit|album).*', '', clean_target)
            
            score = 0
            if clean_item == clean_target:
                score = 100  # Perfect match
            elif core_item == core_target and core_item:
                score = 90   # Core match
            elif clean_target in clean_item or clean_item in clean_target:
                # Substring match: calculate length difference
                len_diff = abs(len(clean_item) - len(clean_target))
                # Reject broad substring matches for extremely short track names (e.g. 'U')
                if min(len(clean_item), len(clean_target)) <= 3:
                    if len_diff == 0:
                        score = 80
                    else:
                        score = 0
                else:
                    score = 50 - len_diff  # Closer lengths rank higher
                    
            if score > 0 and score > best_match_score:
                best_match_score = score
                found_specific_explicit = is_explicit

    # Find other versions
    other_version_id = None
    other_version_title = None
    
    current_album_title = None
    try:
        current_album_title = data['header']['musicDetailHeaderRenderer']['title']['runs'][0]['text']
    except:
        try:
            current_album_title = data.get('title', '')
        except:
            pass
            
    shelves = find_nodes(data, 'musicCarouselShelfRenderer')
    for shelf in shelves:
        try:
            shelf_title = shelf['header']['musicCarouselShelfBasicHeaderRenderer']['title']['runs'][0]['text']
            if 'Other versions' in shelf_title:
                for version_item in shelf.get('contents', []):
                    renderer = version_item.get('musicTwoRowItemRenderer', {})
                    version_title = "Unknown"
                    try:
                        version_title = renderer['title']['runs'][0]['text']
                    except:
                        pass
                        
                    if not current_album_title or version_title.strip().lower() == current_album_title.strip().lower():
                        playlist_ids = find_nodes(renderer, 'playlistId')
                        if playlist_ids:
                            other_version_id = playlist_ids[0]
                            other_version_title = version_title
                            break
                if other_version_id:
                    break
        except:
            pass
            
    return statuses, found_specific_explicit, other_version_id, other_version_title

def search_yt_music_album(artist, title):
    """Searches YouTube Music for the album matching the song and returns the album playlist URL."""
    query = urllib.parse.quote(f"{artist} {title} album")
    search_url = f"https://music.youtube.com/search?q={query}"
    html = get_html_with_headers(search_url)
    if not html:
        return None

    # Try to find playlistId or album details using a simpler regex
    playlist_ids = re.findall(r'OLAK5uy_[A-Za-z0-9_-]+', html)
    if playlist_ids:
        return f"https://music.youtube.com/playlist?list={playlist_ids[0]}"
    return None

def save_csv_database(rows, fieldnames):
    """Safely saves database rows to CSV using a tempfile swap to prevent corruption."""
    try:
        temp_path = CSV_PATH + ".tmp"
        with open(temp_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            
        # Swap files
        backup_path = CSV_PATH + ".bak"
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except:
                pass
        if os.path.exists(CSV_PATH):
            os.rename(CSV_PATH, backup_path)
        os.rename(temp_path, CSV_PATH)
        return True
    except Exception as e:
        print(f"  [ERROR] Failed to save CSV database: {e}")
        return False

def find_clean_track_url(counterpart_album_id, target_title):
    """Fetches the counterpart album page and finds the video URL of the track matching target_title."""
    album_url = f"https://music.youtube.com/playlist?list={counterpart_album_id}"
    html = get_html_with_headers(album_url)
    if not html:
        return None, None
        
    scripts = re.findall(r'<script.*?>.*?</script>', html, re.DOTALL)
    data_script = None
    for script in scripts:
        if 'initialData' in script and 'musicResponsiveListItemRenderer' in script:
            data_script = script
            break
            
    if not data_script:
        return None, None
        
    matches = re.findall(r"data:\s*'([\s\S]*?)'", data_script)
    if len(matches) < 2:
        return None, None
        
    try:
        decoded = matches[1].encode('utf-8').decode('unicode_escape')
        data = json.loads(decoded)
    except:
        return None, None
        
    items = find_nodes(data, 'musicResponsiveListItemRenderer')
    best_match_score = -1
    best_video_id = None
    best_title = None
    
    for item in items:
        title = "Unknown"
        try:
            title = item['flexColumns'][0]['musicResponsiveListItemFlexColumnRenderer']['text']['runs'][0]['text']
        except:
            pass
            
        video_id = None
        try:
            video_id = item['playlistItemData']['videoId']
        except:
            try:
                video_id = item['flexColumns'][0]['musicResponsiveListItemFlexColumnRenderer']['text']['runs'][0]['navigationEndpoint']['watchEndpoint']['videoId']
            except:
                pass
                
        if not video_id:
            continue
            
        # Score the title match
        clean_item = re.sub(r'[^a-z0-9]', '', title.lower())
        clean_target = re.sub(r'[^a-z0-9]', '', target_title.lower())
        
        # Strip common suffixes/versions
        core_item = re.sub(r'(feat|with|remix|mono|single|version|radio|edit|album).*', '', clean_item)
        core_target = re.sub(r'(feat|with|remix|mono|single|version|radio|edit|album).*', '', clean_target)
        
        score = 0
        if clean_item == clean_target:
            score = 100
        elif core_item == core_target and core_item:
            score = 90
        elif clean_target in clean_item or clean_item in clean_target:
            len_diff = abs(len(clean_item) - len(clean_target))
            if min(len(clean_item), len(clean_target)) <= 3:
                score = 80 if len_diff == 0 else 0
            else:
                score = 50 - len_diff
                
        if score > 0 and score > best_match_score:
            best_match_score = score
            best_video_id = video_id
            best_title = title
            
    if best_match_score >= 40 and best_video_id:
        return f"https://music.youtube.com/watch?v={best_video_id}", best_title
    return None, None

def trigger_clean_download(counterpart_url, target_folder, explicit=None):
    import json
    import urllib.request
    
    url = "http://localhost:58000/add"
    payload = {
        "urls": counterpart_url,
        "target": target_folder,
        "auto_linked": True
    }
    if explicit is not None:
        payload["explicit"] = explicit
        
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url, 
        data=data, 
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            if res_data.get("status") == "ok":
                print(f"  [✓ AUTO-DOWNLOAD SUCCESS] Enqueued clean counterpart in downloader queue: {counterpart_url}")
                return True
            else:
                print(f"  [-] Downloader response: {res_data}")
    except Exception as e:
        print(f"  [-] Downloader communication error: {e} (Is app.py running on port 58000?)")
    return False

def audit_library_counterparts(limit=5, query=None, dry_run=True):
    """Audits library tracks to detect explicit status and locate clean counterparts."""
    print(f"Reading library from CSV: {CSV_PATH}")
    if dry_run:
        print("[DRY RUN] Running in dry-run mode. CSV file will not be modified.")
    
    rows = []
    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    # Count how many need auditing (blank or "unknown" explicit status)
    auditable_tracks = []
    for r in rows:
        name = r['Track Name']
        path = r.get('File Path', '')
        explicit_val = r.get('Explicit', '').strip().lower()
        
        # Apply query filter if provided
        if query and query.lower() not in name.lower():
            continue
            
        # Hard exclusion for Danny Boy tracks
        if "danny boy" in name.lower():
            continue
            
        # Exclude non-songs: set Explicit to False if not already, and skip auditing
        if is_non_song(name, path):
            if explicit_val != 'false':
                r['Explicit'] = 'False'
                r['Source_URL'] = ''
                save_csv_database(rows, fieldnames)
            continue
            
        if explicit_val in ['', 'unknown']:
            auditable_tracks.append(r)

    print(f"Found {len(auditable_tracks)} tracks that match your filters and need auditing.")
    
    if not auditable_tracks:
        print("No tracks found matching your query that need auditing.")
        return

    # We will audit a subset of them
    to_audit = auditable_tracks[:limit]
    print(f"Auditing {len(to_audit)} tracks now...")
    
    results_report = []
    consecutive_rate_limits = 0
    
    for idx, r in enumerate(to_audit, 1):
        name = r['Track Name']
        url = r.get('Source_URL', '').strip()
        
        # Parse artist / title
        parts = name.split(" - ", 1)
        artist = parts[0].strip() if len(parts) > 1 else "Unknown"
        title = parts[1].strip() if len(parts) > 1 else name.strip()
        
        print(f"\n[{idx}/{len(to_audit)}] Auditing: {name}")
        
        album_url = None
        is_watch = "watch?v=" in url
        
        # Rate-limiting mitigation: inspect html fetching health
        def fetch_html_with_ratelimit_check(fetch_url):
            nonlocal consecutive_rate_limits
            try:
                html_content = get_html_with_headers(fetch_url)
                consecutive_rate_limits = 0
                return html_content
            except urllib.error.HTTPError as e:
                if e.code in [403, 429]:
                    print(f"  [WARNING] YouTube Music rate-limiting or security block (HTTP {e.code}) detected!")
                    consecutive_rate_limits += 1
                return None
            except Exception as e:
                consecutive_rate_limits += 1
                return None

        if url and ("music.youtube.com" in url or "youtube.com/watch" in url):
            if "playlist?list=" in url:
                album_url = url
            elif is_watch:
                # Scrape watch page to find playlist ID
                html = fetch_html_with_ratelimit_check(url)
                if html:
                    playlist_ids = re.findall(r'OLAK5uy_[A-Za-z0-9_-]+', html)
                    if playlist_ids:
                        album_url = f"https://music.youtube.com/playlist?list={playlist_ids[0]}"
        
        if not album_url:
            # Fallback: search YouTube Music using artist and title
            print(f"  No Source URL found. Searching YTM for: {artist} - {title}")
            # Scrape search page
            query_escaped = urllib.parse.quote(f"{artist} {title} album")
            search_url = f"https://music.youtube.com/search?q={query_escaped}"
            search_html = fetch_html_with_ratelimit_check(search_url)
            if search_html:
                playlist_ids = re.findall(r'OLAK5uy_[A-Za-z0-9_-]+', search_html)
                if playlist_ids:
                    album_url = f"https://music.youtube.com/playlist?list={playlist_ids[0]}"
                    print(f"  [FOUND URL]: {album_url}")
                    if not dry_run:
                        r['Source_URL'] = album_url
                else:
                    print("  Could not find any album playlists in search results.")
            
        if consecutive_rate_limits >= 3:
            print("\n[CRITICAL] Too many consecutive network/rate-limiting errors. Saving progress and stopping to protect your IP address.")
            break

        if album_url:
            print(f"  Album URL: {album_url}")
            statuses, found_specific_explicit, counterpart_id, counterpart_title = audit_album_page(album_url, target_title=title)
            
            if statuses:
                is_explicit = False
                if is_watch:
                    # Check watch page HTML directly for the explicit badge
                    watch_html = fetch_html_with_ratelimit_check(url)
                    if watch_html and 'MUSIC_EXPLICIT_BADGE' in watch_html:
                        is_explicit = True
                else:
                    if found_specific_explicit is not None:
                        is_explicit = found_specific_explicit
                    else:
                        is_explicit = any(statuses)
                
                print(f"  Explicit Status: {'EXPLICIT' if is_explicit else 'CLEAN'}")
                
                if not dry_run:
                    # Update row in memory
                    r['Explicit'] = 'True' if is_explicit else 'False'
                    # Save CSV instantly to preserve progress
                    save_csv_database(rows, fieldnames)
                else:
                    print(f"  [DRY RUN] Would set Explicit = {'True' if is_explicit else 'False'}")
                
                if is_explicit:
                    if counterpart_id:
                        print(f"  [FOUND CLEAN COUNTERPART ALBUM]: \"{counterpart_title}\"")
                        clean_track_url, clean_track_title = find_clean_track_url(counterpart_id, title)
                        
                        if clean_track_url:
                            print(f"  [FOUND SPECIFIC CLEAN TRACK]: \"{clean_track_title}\"")
                            print(f"  URL: {clean_track_url}")
                            results_report.append({
                                'Song': name,
                                'Album': counterpart_title,
                                'URL': clean_track_url
                            })
                            
                            # Trigger automatic clean download replacement
                            if not dry_run:
                                file_path = r.get('File Path', '')
                                file_path_clean = file_path.replace("\\", "/")
                                parts = file_path_clean.split("/")
                                target_folder = "New School 2010+" # fallback
                                if len(parts) >= 2:
                                    target_folder = parts[-2]
                                    
                                print(f"  [AUTO-DOWNLOAD] Triggering clean replacement download in target: {target_folder}...")
                                trigger_clean_download(clean_track_url, target_folder, explicit=False)
                        else:
                            print("  [-] Could not resolve specific clean track in the counterpart album. Skipping download.")
                    else:
                        print("  No counterpart clean album found in 'Other versions' shelf.")
            else:
                print("  Failed to extract album data.")
                if not dry_run:
                    r['Explicit'] = 'False'
                    save_csv_database(rows, fieldnames)
        else:
            print("  Could not resolve album URL.")
            if not dry_run:
                r['Explicit'] = 'False'
                save_csv_database(rows, fieldnames)
            
        time.sleep(1.8) # rate limit politeness

    if results_report:
        print("\n=== CLEAN COUNTERPARTS SUMMARY REPORT ===")
        for item in results_report:
            print(f"- {item['Song']} ➔ Clean Counterpart Album: \"{item['Album']}\"")
            print(f"  Download Link: {item['URL']}")
            print("  (Paste this URL in FMP Ultimate dashboard to download the clean version!)")
    else:
        print("\nNo new counterparts found or audited songs were clean.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Find clean counterparts for explicit library tracks.")
    parser.add_argument("limit", type=int, nargs="?", default=5, help="Number of tracks to audit")
    parser.add_argument("--query", type=str, default=None, help="Query to filter tracks by name/artist")
    parser.add_argument("--live", action="store_true", help="Actually write CSV changes and trigger downloads (default is a dry run)")

    args = parser.parse_args()
    audit_library_counterparts(limit=args.limit, query=args.query, dry_run=not args.live)
