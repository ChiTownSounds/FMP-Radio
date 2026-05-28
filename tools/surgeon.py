import os
import sys
import csv
import shutil
import time

# Establish pathing
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from modules.download import Transporter
from modules.tagger import AutoMaster

CSV_PATH = os.path.join(parent_dir, "configs", "fmp_data_7718.csv")
MANIFEST_PATH = os.path.join(parent_dir, "REPLACEMENT_NEEDED.csv")

def main():
    print("Starting FMP Surgeon Engine...")
    
    if not os.path.exists(MANIFEST_PATH) or not os.path.exists(CSV_PATH):
        print("Error: Missing CSV files. Check paths.")
        return

    targets = []
    with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            targets.append(row)

    transporter = Transporter()
    automaster = AutoMaster()
    successful_replacements = 0

    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        master_db = list(csv.DictReader(f))
    master_fieldnames = master_db[0].keys() if master_db else []

    for idx, target in enumerate(targets):
        track_name = target.get('Track Name', '')
        original_path = target.get('File Path', '')
        
        if not track_name or not original_path: continue
            
        print(f"\n[{idx+1}/{len(targets)}] Processing: {track_name}")
        search_query = f"ytmsearch1:{track_name}"
        
        try:
            # Bulletproof dynamic data capture
            result = transporter.download_track(url=search_query, task_id=f"batch_{idx}")
            
            # Extract just the path, whether it's a tuple or a raw string
            downloaded_path = result[0] if isinstance(result, tuple) else result
            
            if not downloaded_path or not os.path.exists(downloaded_path):
                print("  !! Download failed or file not found.")
                continue
                
            print(f"  -> Download complete. Swapping out damaged file on Z:/...")
            if os.path.exists(original_path):
                os.remove(original_path)
            
            os.makedirs(os.path.dirname(original_path), exist_ok=True)
            shutil.move(downloaded_path, original_path)
            
            print("  -> Analyzing new audio for precision cue points...")
            metrics = automaster._analyze_audio_properties(original_path)
            
            new_intro = metrics.get('intro_sec', 0.0)
            new_punch = int(metrics.get('cue_in', 0.0) * 1000)
            
            for row in master_db:
                if row.get('File Path') == original_path:
                    row['Intro_Duration'] = str(new_intro)
                    row['Punch_Ms'] = str(new_punch)
                    break
            
            print(f"  -> Success! Intro: {new_intro}s | Punch: {new_punch}ms")
            successful_replacements += 1
            
            if successful_replacements % 10 == 0:
                print("  [Checkpoint] Saving master database...")
                with open(CSV_PATH, 'w', encoding='utf-8', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=master_fieldnames)
                    writer.writeheader()
                    writer.writerows(master_db)
            
            time.sleep(3)
            
        except Exception as e:
            print(f"  !! Error: {e}")

    print("\nBatch Complete. Saving final database...")
    with open(CSV_PATH, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=master_fieldnames)
        writer.writeheader()
        writer.writerows(master_db)

if __name__ == "__main__":
    main()