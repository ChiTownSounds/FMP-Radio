import os
import sys
import csv
import re
import urllib.request
import json
import codecs
import time

sys.stdout.reconfigure(encoding='utf-8')

CSV_PATH = r"C:\FMP_Ultimate\configs\fmp_data_7718.csv"

def get_html_with_headers(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.read().decode('utf-8', errors='ignore')
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

def audit_album_page(album_url):
    """Scrapes a YouTube Music album playlist page to get track explicit statuses and counterparts."""
    html = get_html_with_headers(album_url)
    if not html:
        return None, None, None

    # Parse initialData
    scripts = re.findall(r'<script.*?>.*?</script>', html, re.DOTALL)
    data_script = None
    for script in scripts:
        if 'initialData' in script and ('MUSIC_EXPLICIT_BADGE' in script or 'musicResponsiveListItemRenderer' in script):
            data_script = script
            break
            
    if not data_script:
        return None, None, None
        
    matches = re.findall(r"data:\s*'([\s\S]*?)'", data_script)
    if len(matches) < 2:
        return None, None, None
        
    try:
        decoded = matches[1].encode('utf-8').decode('unicode_escape')
        data = json.loads(decoded)
    except Exception as e:
        print(f"JSON decode failed: {e}")
        return None, None, None

    # Extract explicit statuses
    items = find_nodes(data, 'musicResponsiveListItemRenderer')
    statuses = []
    for item in items:
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
            
    return statuses, other_version_id, other_version_title

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

def audit_library_counterparts(limit=5, query=None, dry_run=False):
    """Audits library tracks with Source_URLs to detect explicit status and locate clean counterparts."""
    print(f"Reading library from CSV: {CSV_PATH}")
    if dry_run:
        print("[DRY RUN] Running in dry-run mode. CSV file will not be modified.")
    
    rows = []
    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    # Let's count how many have YTM URLs and need auditing
    auditable_tracks = []
    for r in rows:
        name = r['Track Name']
        url = r.get('Source_URL', '').strip()
        explicit_val = r.get('Explicit', '').strip().lower()
        
        # Apply query filter if provided
        if query and query.lower() not in name.lower():
            continue
            
        # Check if the song has a YTM URL and is not yet audited (blank or "unknown")
        if url and ("music.youtube.com" in url or "youtube.com/watch" in url):
            if explicit_val in ['', 'unknown']:
                auditable_tracks.append(r)

    print(f"Found {len(auditable_tracks)} tracks with source URLs that match your filters and need auditing.")
    
    if not auditable_tracks:
        print("No tracks found matching your query that need auditing.")
        return

    # We will audit a subset of them
    to_audit = auditable_tracks[:limit]
    print(f"Auditing {len(to_audit)} tracks now...")
    
    results_report = []
    updated_rows = {row['Track Name']: row for row in rows}
    csv_updated = False
    
    for idx, r in enumerate(to_audit, 1):
        name = r['Track Name']
        url = r['Source_URL']
        
        # Parse artist / title
        parts = name.split(" - ", 1)
        artist = parts[0].strip() if len(parts) > 1 else "Unknown"
        title = parts[1].strip() if len(parts) > 1 else name.strip()
        
        print(f"\n[{idx}/{len(to_audit)}] Auditing: {name}")
        
        album_url = None
        is_watch = "watch?v=" in url
        
        if "playlist?list=" in url:
            album_url = url
        elif is_watch:
            # Scrape watch page to find playlist ID
            html = get_html_with_headers(url)
            if html:
                playlist_ids = re.findall(r'OLAK5uy_[A-Za-z0-9_-]+', html)
                if playlist_ids:
                    album_url = f"https://music.youtube.com/playlist?list={playlist_ids[0]}"
        
        if not album_url:
            # Fallback: search YouTube Music using artist and title
            album_url = search_yt_music_album(artist, title)

        if album_url:
            print(f"  Album URL: {album_url}")
            statuses, counterpart_id, counterpart_title = audit_album_page(album_url)
            
            if statuses:
                is_explicit = False
                if is_watch:
                    # Check watch page HTML directly for the explicit badge
                    watch_html = get_html_with_headers(url)
                    if watch_html and 'MUSIC_EXPLICIT_BADGE' in watch_html:
                        is_explicit = True
                else:
                    is_explicit = any(statuses)
                
                print(f"  Explicit Status: {'EXPLICIT' if is_explicit else 'CLEAN'}")
                
                # Update CSV row
                r['Explicit'] = 'True' if is_explicit else 'False'
                updated_rows[name] = r
                csv_updated = True
                
                if is_explicit:
                    if counterpart_id:
                        counterpart_url = f"https://music.youtube.com/playlist?list={counterpart_id}"
                        print(f"  [FOUND CLEAN COUNTERPART]: \"{counterpart_title}\"")
                        print(f"  URL: {counterpart_url}")
                        results_report.append({
                            'Song': name,
                            'Album': counterpart_title,
                            'URL': counterpart_url
                        })
                    else:
                        print("  No counterpart clean album found in 'Other versions' shelf.")
            else:
                print("  Failed to extract album data.")
        else:
            print("  Could not resolve album URL.")
            
        time.sleep(1.5) # rate limit politeness

    # Write updates to CSV if any
    if csv_updated and not dry_run:
        with open(CSV_PATH, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
        print("\n[UPDATED] Master CSV database updated with verified Explicit/Clean tags.")
    elif csv_updated and dry_run:
        print("\n[DRY RUN] Skipping database CSV update.")

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
    parser.add_argument("--dry-run", action="store_true", help="Run without writing changes to the CSV database")
    
    args = parser.parse_args()
    audit_library_counterparts(limit=args.limit, query=args.query, dry_run=args.dry_run)
