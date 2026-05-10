import csv
from pathlib import Path

# Try to import your custom utilities for enriched metadata
try:
    import fmp_library
    HAS_LIBRARY = True
except ImportError:
    HAS_LIBRARY = False

# --- CONFIGURATION ---
BASE_DIR = Path(r"C:\FMP_Ultimate")
TARGET_CSV = BASE_DIR / "configs" / "fmp_data_7718.csv"

# The "Hit List": Only scan these specific era folders
TARGET_FOLDERS = [
    Path(r"Z:\Classics"),
    Path(r"Z:\New School 2010+"),
    Path(r"Z:\Old School 70s80s"),
    Path(r"Z:\Throwback 90s2000s"),
    Path(r"Z:\Live")
]

def build_super_baseline():
    """
    Scans the specific era folders on the Z: drive and builds the Master CSV.
    """
    print("[SCAN] Initializing High-Intelligence probe on Z: drive...")
    
    actual_tracks = []
    total_found = 0
    
    for folder in TARGET_FOLDERS:
        if not folder.exists():
            print(f"[WARNING] Cannot find folder: {folder} - Skipping.")
            continue
            
        print(f" -> Scanning: {folder.name}...")
        
        # Scan all mp3s in this specific folder
        for file_path in folder.rglob("*.mp3"):
            track_name = file_path.stem
            
            # Use fmp_library if available, otherwise fallback to defaults
            if HAS_LIBRARY:
                try:
                    meta = fmp_library.get_enriched_metadata(file_path)
                    kbps = meta.get('kbps') or 320
                    has_lyrics = meta.get('lyrics') or False
                    art_ratio = meta.get('art_ratio') or 0
                    year = meta.get('year') or ""
                except:
                    kbps = 320; has_lyrics = False; art_ratio = 0; year = ""
            else:
                kbps = 320; has_lyrics = False; art_ratio = 0; year = ""

            actual_tracks.append({
                "Track Name": track_name,
                "Bitrate": kbps,
                "Lyrics": has_lyrics,
                "Year": year,
                "Art Ratio": round(art_ratio, 2) if art_ratio else 0
            })
            total_found += 1

    if total_found == 0:
        print("[ERROR] No tracks found in any of the specified Z: folders.")
        return

    # Ensure the configs folder exists
    TARGET_CSV.parent.mkdir(parents=True, exist_ok=True)

    # Write the new physical reality to the CSV
    try:
        with open(TARGET_CSV, 'w', encoding='utf-8', newline='') as f:
            fieldnames = ["Track Name", "Bitrate", "Lyrics", "Year", "Art Ratio"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for track in sorted(actual_tracks, key=lambda x: x["Track Name"]):
                writer.writerow(track)
                
        print(f"[SUCCESS] Super-Blueprint rebuilt! {total_found} tracks perfectly indexed.")
    except Exception as e:
        print(f"[ERROR] Failed to write CSV: {e}")

if __name__ == "__main__":
    print("=== FMP BASELINE SYNC (MULTI-FOLDER Z: EDITION) ===")
    build_super_baseline()