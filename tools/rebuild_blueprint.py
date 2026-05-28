import os
import csv
import sys
import itertools
from pathlib import Path
from mutagen.mp3 import MP3

CSV_PATH = Path("configs/fmp_data_7718.csv")
SERVER_DIR = Path("Z:/")
ERA_FOLDERS = ["Classics", "Old School 70s80s", "Throwbacks 90s2000s", "New School 2010+", "Live", "Unsorted_Review"]

def rebuild():
    print("Rebuilding 7718 Blueprint...")
    new_rows = []
    spinner = itertools.cycle(['|', '/', '-', '\\'])
    
    # 0. Cache existing metadata to prevent data loss
    cached_metadata = {}
    if CSV_PATH.exists():
        try:
            with open(CSV_PATH, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    track_name = row.get('Track Name')
                    if track_name:
                        cached_metadata[track_name] = {
                            'Energy Category': row.get('Energy Category', 'Unassigned'),
                            'Intro Sec': row.get('Intro Sec', '0')
                        }
        except Exception as e:
            print(f"Warning: Could not read existing CSV to cache metadata: {e}")
            
    # 1. Count total files first so we can track progress
    total_files = 0
    for folder in ERA_FOLDERS:
        target = SERVER_DIR / folder
        if target.exists():
            total_files += len(list(target.glob("*.mp3")))
            
    if total_files == 0:
        print("No .mp3 files found in the era folders.")
        return

    # 2. Process files with live visual feedback
    processed = 0
    for folder in ERA_FOLDERS:
        target = SERVER_DIR / folder
        if not target.exists(): continue
        
        for file in target.glob("*.mp3"):
            processed += 1
            
            # The Spinner and Counter
            sys.stdout.write(f"\r[SCANNING {next(spinner)}] {processed}/{total_files} - {file.name[:35]}...".ljust(80))
            sys.stdout.flush()
            
            try:
                audio = MP3(file)
                length = f"{int(audio.info.length // 60)}:{int(audio.info.length % 60):02d}"
                year = "Unknown"
                if audio.tags:
                    year_tag = audio.tags.get('TDRC') or audio.tags.get('TYER')
                    if year_tag: year = str(year_tag.text[0])[:4]
                
                track_name = file.stem
                meta = cached_metadata.get(track_name, {'Energy Category': 'Unassigned', 'Intro Sec': '0'})
                
                new_rows.append({
                    'Track Name': track_name,
                    'Bitrate': "320",
                    'Lyrics': "Unknown",
                    'Year': year,
                    'Art Ratio': "1.0",
                    'Length': length,
                    'Source_URL': "",
                    'Energy Category': meta.get('Energy Category', 'Unassigned'),
                    'Intro Sec': meta.get('Intro Sec', '0')
                })
            except: pass

    # Clear the spinner line
    sys.stdout.write("\r" + " " * 80 + "\r")
    sys.stdout.flush()
    
    # 3. Write to the CSV
    print("\nWriting to CSV...")
    with open(CSV_PATH, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['Track Name', 'Bitrate', 'Lyrics', 'Year', 'Art Ratio', 'Length', 'Source_URL', 'Energy Category', 'Intro Sec'])
        writer.writeheader()
        writer.writerows(new_rows)
        
    print(f"Done! {len(new_rows)} tracks synced. Search is now active.")

if __name__ == "__main__":
    rebuild()