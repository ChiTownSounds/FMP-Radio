import os
import sys
import csv
import re
import urllib.request
import urllib.parse
import json
import time
import argparse

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

def parse_time_str(time_str):
    if not time_str:
        return 0
    parts = time_str.strip().split(':')
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except:
        pass
    return 0

# Using is_non_song imported from config

def clean_artist_set(artist_name):
    # Split by standard feature separators and punctuation to get base artists
    artists = re.split(r'[,/;&]|\bfeat\b|\bwith\b|\band\b', artist_name.lower())
    return {re.sub(r'[^a-z0-9]', '', a.strip()) for a in artists if a.strip()}

def trigger_clean_download(counterpart_url, target_folder, explicit=False):
    url = "http://localhost:5000/add"
    payload = {
        "urls": counterpart_url,
        "target": target_folder,
        "auto_linked": True,
        "explicit": explicit
    }
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
                print(f"  [✓ AUTO-DOWNLOAD SUCCESS] Enqueued clean counterpart: {counterpart_url}")
                return True
            else:
                print(f"  [-] Downloader response: {res_data}")
    except Exception as e:
        print(f"  [-] Downloader communication error: {e} (Is app.py running?)")
    return False

def extract_artists_from_renderer(item):
    artists = []
    # Search all columns for runs that are artists
    for col in item.get('flexColumns', []):
        try:
            runs = col['musicResponsiveListItemFlexColumnRenderer']['text']['runs']
            for run in runs:
                text = run.get('text', '').strip()
                if not text or text in ['Song', 'Video', '•', 'Artist'] or 'plays' in text or 'views' in text:
                    continue
                # If browse endpoint is artist or UC ID is present, it's an artist
                nav = run.get('navigationEndpoint', {})
                browse = nav.get('browseEndpoint', {})
                page_type = browse.get('browseEndpointContextSupportedConfigs', {}).get('browseEndpointContextMusicConfig', {}).get('pageType')
                if page_type == 'MUSIC_PAGE_TYPE_ARTIST' or browse.get('browseId', '').startswith('UC'):
                    artists.append(text)
        except:
            pass
    return artists

def search_clean_version(artist, title, original_seconds):
    # Construct search queries: Clean first, then fallback to radio edit
    queries = [
        f"{artist} {title} clean",
        f"{artist} {title} radio edit"
    ]
    
    for query in queries:
        query_escaped = urllib.parse.quote(query)
        search_url = f"https://music.youtube.com/search?q={query_escaped}"
        html = get_html_with_headers(search_url)
        if not html:
            continue
            
        scripts = re.findall(r'<script.*?>.*?</script>', html, re.DOTALL)
        data_script = None
        for script in scripts:
            if 'initialData' in script and 'musicResponsiveListItemRenderer' in script:
                data_script = script
                break
                
        if not data_script:
            continue
            
        matches = re.findall(r"data:\s*'([\s\S]*?)'", data_script)
        if len(matches) < 2:
            continue
            
        try:
            raw_data = matches[1].replace('\\/', '/')
            decoded = raw_data.encode('utf-8').decode('unicode_escape')
            data = json.loads(decoded)
        except Exception as e:
            continue
            
        items = find_nodes(data, 'musicResponsiveListItemRenderer')
        if not items:
            continue
            
        target_artists = clean_artist_set(artist)
        candidates = []
        
        for item in items:
            try:
                # 1. Title
                res_title = item['flexColumns'][0]['musicResponsiveListItemFlexColumnRenderer']['text']['runs'][0]['text']
                
                # 2. Artist
                res_artists = extract_artists_from_renderer(item)
                res_artist_str = ", ".join(res_artists)
                candidate_artists = clean_artist_set(res_artist_str)
                
                # Skip karaoke, tribute, cover, or instrumental versions
                t_lower = res_title.lower()
                a_lower = res_artist_str.lower()
                exclude_keywords = ["karaoke", "tribute", "instrumental", "backing track", "originally performed", "originally by", "cover version", "piano cover", "acoustic cover"]
                if any(k in t_lower or k in a_lower for k in exclude_keywords):
                    continue
                
                # 3. Explicit Badge
                is_explicit = False
                badges = item.get('badges', [])
                for badge in badges:
                    badge_type = badge.get('musicInlineBadgeRenderer', {}).get('icon', {}).get('iconType')
                    if badge_type == 'MUSIC_EXPLICIT_BADGE':
                        is_explicit = True
                
                # Skip if explicit
                if is_explicit:
                    continue
                    
                # 4. VideoId
                video_id = item['playlistItemData']['videoId']
                
                # 5. Duration and Item Type (extract from any column if available)
                res_duration = ""
                item_type = "Unknown"
                for col in item.get('flexColumns', []):
                    try:
                        text_runs = col['musicResponsiveListItemFlexColumnRenderer']['text']['runs']
                        for tr in text_runs:
                            text_val = tr.get('text', '').strip()
                            if ':' in text_val and re.match(r'^\d+:\d+$', text_val):
                                res_duration = text_val
                            elif text_val in ['Song', 'Video']:
                                item_type = text_val
                    except:
                        pass
                
                # Version/Remix checks: make sure "remix" and "live" statuses match
                original_has_remix = "remix" in title.lower() or "remix" in artist.lower()
                candidate_has_remix = "remix" in res_title.lower() or "remix" in res_artist_str.lower()
                if original_has_remix != candidate_has_remix:
                    continue
                    
                original_has_live = "live" in title.lower()
                candidate_has_live = "live" in res_title.lower()
                if original_has_live != candidate_has_live:
                    continue
                
                # Validate Title Match
                clean_item = re.sub(r'[^a-z0-9]', '', res_title.lower())
                clean_target = re.sub(r'[^a-z0-9]', '', title.lower())
                core_item = re.sub(r'(feat|with|remix|mono|single|version|radio|edit|album).*', '', clean_item)
                core_target = re.sub(r'(feat|with|remix|mono|single|version|radio|edit|album).*', '', clean_target)
                
                title_score = 0
                if clean_item == clean_target:
                    title_score = 100
                elif core_item == core_target and core_item:
                    title_score = 90
                elif clean_target in clean_item or clean_item in clean_target:
                    len_diff = abs(len(clean_item) - len(clean_target))
                    if min(len(clean_item), len(clean_target)) > 3:
                        title_score = 50 - len_diff
                
                if title_score < 40:
                    continue
                    
                # Validate Artist Overlap
                artist_match = bool(target_artists & candidate_artists)
                if not artist_match:
                    # Fallback string containment checks
                    artist_match = any(ta in res_artist_str.lower() or ca in artist.lower() for ta in target_artists for ca in candidate_artists)
                
                if not artist_match:
                    # Allow matching if it's a Topic channel or official track where the search term contains the artist
                    if any(ta in res_title.lower() for ta in target_artists) and (item_type == 'Song' or 'topic' in res_artist_str.lower()):
                        artist_match = True
                
                if not artist_match:
                    continue
                    
                # Validate Duration
                candidate_seconds = parse_time_str(res_duration)
                dur_diff = 999
                if original_seconds > 0 and candidate_seconds > 0:
                    dur_diff = abs(original_seconds - candidate_seconds)
                    if dur_diff > 35: # allow up to 35 seconds difference
                        continue
                
                # Compute Ranking Score
                ranking_score = title_score
                
                # Skip video versions per user rule
                if item_type == 'Video':
                    continue
                
                # Song vs Video boost
                if item_type == 'Song':
                    ranking_score += 100
                    
                # Official Artist Channel vs Lyric/Fan/Uploader channel
                # Check for channel fallback words in the candidate artist string
                is_fallback = any(kw in res_artist_str.lower() for kw in ['topic', 'lyrics', 'channel', 'vevo', 'upload', 'lyrics'])
                if is_fallback:
                    # Topic channels are official automated releases, so they are fine if the item_type is 'Song'
                    if 'topic' in res_artist_str.lower() and item_type == 'Song':
                        ranking_score += 20 # slight boost for official topic song release
                    else:
                        ranking_score -= 50 # penalty for non-song fallback channels
                else:
                    ranking_score += 50 # boost for actual artist name in candidate artist
                    
                # Duration match closeness bonus
                if dur_diff <= 5:
                    ranking_score += 15
                elif dur_diff <= 15:
                    ranking_score += 5
                    
                # Collect candidate
                candidates.append((ranking_score, f"https://music.youtube.com/watch?v={video_id}", res_title, res_artist_str, res_duration, item_type))
            except Exception:
                pass
                
        # If we have valid candidates, sort by score descending and return the best one
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            best_cand = candidates[0]
            print(f"  [MATCHED] Best clean candidate (Score={best_cand[0]}, Type={best_cand[5]}): '{best_cand[2]}' by '{best_cand[3]}' (URL: {best_cand[1]})")
            return best_cand[1], best_cand[2], best_cand[3], best_cand[4]
            
        time.sleep(1.0)
    return None

def process_explicit_upgrades(limit=5, dry_run=True):
    print("Reading master CSV database...")
    rows = []
    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
        
    def get_core_track_key(name):
        if not name:
            return ""
        import unicodedata
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        parts = name.split(' - ', 1)
        if len(parts) == 2:
            artist, title = parts
        else:
            artist = ""
            title = name
            
        artist_clean = artist.lower()
        artist_clean = re.split(r'\s+(feat\.?|featuring|with|w/|f/|and|&)\s+', artist_clean)[0]
        artist_clean = re.sub(r'[^a-z0-9]', '', artist_clean)
        
        title_clean = title.lower()
        title_clean = re.sub(r'\[.*?\]', '', title_clean)
        title_clean = re.sub(r'\((?:feat\.?|featuring|f/)\.?\s+.*?\)', '', title_clean)
        title_clean = re.sub(r'[^a-z0-9]', '', title_clean)
        core_title = re.sub(r'(feat|with|remix|mono|single|version|radio|edit|album|clean).*', '', title_clean)
        
        return f"{artist_clean}_{core_title}"

    # Build set of existing clean counterparts
    existing_clean_keys = set()
    for row in rows:
        explicit_val = row.get('Explicit', '').strip().lower()
        if explicit_val in ['false', '0']:
            name = row.get('Track Name', '')
            if name:
                existing_clean_keys.add(get_core_track_key(name))

    explicit_tracks = []
    non_songs_to_fix = []
    
    for row in rows:
        track_name = row.get('Track Name', '')
        path = row.get('File Path', '')
        explicit_val = row.get('Explicit', '').strip().lower()
        
        
        if is_non_song(track_name, path):
            if explicit_val == 'true':
                non_songs_to_fix.append(row)
            continue
            
        if explicit_val == 'true':
            # Skip if we already have a clean counterpart in the library
            key = get_core_track_key(track_name)
            if key in existing_clean_keys:
                continue
            explicit_tracks.append(row)
            
    print(f"Found {len(non_songs_to_fix)} non-songs incorrectly marked as explicit.")
    print(f"Found {len(explicit_tracks)} actual explicit music tracks lacking clean counterparts.")
    
    # 1. Fix non-songs instantly in database if not dry run
    if non_songs_to_fix:
        print("\nCorrecting non-songs in database:")
        for ns in non_songs_to_fix:
            print(f"  Fixing: {ns.get('Track Name')}")
            if not dry_run:
                ns['Explicit'] = 'False'
                ns['Source_URL'] = ''
        if not dry_run:
            # Save CSV
            temp_path = CSV_PATH + ".tmp"
            with open(temp_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            os.replace(temp_path, CSV_PATH)
            print("[✓] Non-songs fixed in database.")
            
    # 2. Process explicit upgrades
    to_process = explicit_tracks[:limit]
    print(f"\nProcessing counterpart upgrades for {len(to_process)} explicit tracks:")
    
    successful_upgrades = 0
    
    for idx, r in enumerate(to_process, 1):
        name = r['Track Name']
        path = r['File Path']
        duration_ms = r.get('duration_ms', '0')
        length = r.get('Length', '')
        
        # Parse duration
        original_seconds = 0
        if duration_ms and duration_ms.isdigit() and int(duration_ms) > 0:
            original_seconds = int(duration_ms) // 1000
        else:
            original_seconds = parse_time_str(length)
            
        parts = name.split(" - ", 1)
        artist = parts[0].strip() if len(parts) > 1 else "Unknown"
        title = parts[1].strip() if len(parts) > 1 else name.strip()
        
        print(f"\n[{idx}/{len(to_process)}] Scoping clean counterpart for: {artist} - {title}")
        print(f"  Original Path: {path}")
        print(f"  Original Duration: {original_seconds} seconds")
        
        try:
            match = search_clean_version(artist, title, original_seconds)
            if match:
                clean_url, match_title, match_artist, match_dur = match
                print(f"  [FOUND MATCH] Clean Version: {match_artist} - {match_title} ({match_dur})")
                print(f"  URL: {clean_url}")
                
                if dry_run:
                    print(f"  [DRY RUN] Would enqueue download for: {clean_url}")
                else:
                    # Get target folder
                    path_clean = path.replace("\\", "/")
                    path_parts = path_clean.split("/")
                    target_folder = "Throwbacks 90s2000s" # default
                    if len(path_parts) >= 2:
                        target_folder = path_parts[-2]
                        
                    print(f"  [LIVE] Enqueueing upgrade download in folder '{target_folder}'...")
                    success = trigger_clean_download(clean_url, target_folder, explicit=False)
                    if success:
                        successful_upgrades += 1
            else:
                print("  [NO MATCH] Could not find a matching clean counterpart track.")
        except Exception as e:
            print(f"  [ERROR] Counterpart search failed: {e}")
            
        # Rate limit politeness
        time.sleep(3.0)
        
    print(f"\nUpgrade run complete. Total successfully enqueued: {successful_upgrades}/{len(to_process)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean counterpart direct search and upgrade pipeline.")
    parser.add_argument("--limit", type=int, default=5, help="Number of explicit tracks to audit/upgrade")
    parser.add_argument("--live", action="store_true", help="Execute the actual download upgrade trigger (default: dry run)")
    args = parser.parse_args()
    
    dry_run = not args.live
    print(f"Running in {'DRY RUN' if dry_run else 'LIVE'} mode.")
    process_explicit_upgrades(limit=args.limit, dry_run=dry_run)
