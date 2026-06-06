import os
import csv
import sys
import io
from pathlib import Path
from mutagen.mp3 import MP3
from thefuzz import fuzz

# Fix encoding for Windows consoles
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

CSV_PATH = Path(r"c:\FMP_Ultimate\configs\fmp_data_7718.csv")
G_DRIVE_BASE = Path(r"G:\My Drive\FMP MUSIC\BASE\MUSIC")

def check_mismatches():
    print("=" * 60)
    print(" FMP MISMATCH DETECTOR: SCANNING G: DRIVE FOR TAG MISMATCHES")
    print("=" * 60)

    if not CSV_PATH.exists():
        print(f"[FATAL] Could not find CSV at {CSV_PATH}")
        return

    mismatches = []
    scanned = 0
    missing = 0

    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            track_name = row.get("Track Name", "").strip()
            z_path = row.get("File Path", "").strip()

            if not track_name or not z_path:
                continue

            # Convert Z:/ path to G:/ path
            if z_path.upper().startswith("Z:/"):
                rel_path = z_path[3:]
            elif z_path.upper().startswith("Z:\\"):
                rel_path = z_path[3:]
            else:
                rel_path = z_path

            g_path = G_DRIVE_BASE / rel_path

            if not g_path.exists():
                missing += 1
                continue

            try:
                audio = MP3(g_path)
                id3_artist = ""
                id3_title = ""

                if audio.tags:
                    if 'TPE1' in audio.tags:
                        id3_artist = str(audio.tags['TPE1'].text[0]).strip()
                    if 'TIT2' in audio.tags:
                        id3_title = str(audio.tags['TIT2'].text[0]).strip()

                if not id3_artist and not id3_title:
                    continue  # No ID3 tags to compare against, likely pure yt-dlp download

                id3_full = f"{id3_artist} - {id3_title}"
                
                # Use fuzz ratio to compare the expected filename vs the internal ID3 tags
                # token_set_ratio is resilient to extra words like "[Clean]" or "(feat.)"
                ratio = fuzz.token_set_ratio(track_name.lower(), id3_full.lower())

                # A ratio below 50 generally means completely different words
                if ratio < 50:
                    mismatches.append((track_name, id3_full, ratio, g_path))

            except Exception as e:
                pass  # Skip corrupted files or files mutagen can't read

            scanned += 1
            if scanned % 50 == 0:
                print(f"[PROGRESS] Scanned {scanned} tracks... Found {len(mismatches)} mismatches.")

    print("\n" + "=" * 60)
    print(" SCAN COMPLETE ")
    print("=" * 60)
    print(f"Total Scanned: {scanned}")
    print(f"Total Missing from G: Drive: {missing}")
    print(f"Total Mismatches Found: {len(mismatches)}")
    print("-" * 60)

    if mismatches:
        # Sort mismatches by the lowest score (most severe) first
        mismatches.sort(key=lambda x: x[2])
        
        log_path = Path(r"c:\FMP_Ultimate\logs\mismatches.txt")
        log_path.parent.mkdir(exist_ok=True)
        
        with open(log_path, 'w', encoding='utf-8') as lf:
            for expected, actual, score, p in mismatches:
                msg = f"[SEVERE: {score}%] Expected: {expected}\n             Found ID3: {actual}\n             Path: {p}\n"
                print(msg)
                lf.write(msg + "\n")
        
        print(f"\n[!] A full list has been saved to: {log_path}")
    else:
        print("\n[✓] No severe metadata mismatches detected!")

if __name__ == "__main__":
    check_mismatches()
