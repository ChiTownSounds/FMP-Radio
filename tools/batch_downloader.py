import os
import sys
import csv
import time
import shutil

# 1. Establish the absolute path to the root FMP_Broadcaster directory
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)

# 2. Append the root directory to sys.path to allow module imports
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# 3. Import local modules
try:
    from modules.download import Transporter
    from modules.tagger import AutoMaster
except ImportError as e:
    print(f"Warning: Could not import required modules. Error: {e}")

# Helper to safely extract path from potentially tupled return
def get_path_from_result(result):
    if isinstance(result, (list, tuple)):
        for item in result:
            if isinstance(item, str) and os.path.exists(item):
                return item
        return result[0]
    return result

CSV_PATH = os.path.join(parent_dir, "configs", "fmp_data_7718.csv")

def main():
    replacement_csv_path = os.path.join(parent_dir, "REPLACEMENT_NEEDED.csv")

    if not os.path.exists(replacement_csv_path):
        print(f"Error: Could not find {replacement_csv_path}")
        return

    master_rows = []
    master_fieldnames = []
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            master_fieldnames = list(reader.fieldnames) if reader.fieldnames else []
            if 'Intro_Duration' not in master_fieldnames:
                master_fieldnames.append('Intro_Duration')
            if 'Punch_Ms' not in master_fieldnames:
                master_fieldnames.append('Punch_Ms')
            master_rows = list(reader)

    with open(replacement_csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        replacements = list(reader)

    print(f"Starting batch download for {len(replacements)} tracks...")

    try:
        transporter = Transporter()
        automaster = AutoMaster()
    except Exception as e:
        print(f"Engines could not be instantiated: {e}")
        return

    success_count = 0

    for idx, row in enumerate(replacements):
        track_name = row.get('Track Name')
        z_drive_path = row.get('File Path')

        if not track_name:
            continue

        print(f"[{idx+1}/{len(replacements)}] Processing: {track_name}")

        search_query = f"ytmsearch1:{track_name}"
        
        try:
            raw_result = transporter.download_track(url=search_query)
            final_downloaded_path = get_path_from_result(raw_result)
        except Exception as e:
            print(f"  ❌ Download Failed: {e}")
            continue
            
        if not final_downloaded_path or not os.path.exists(final_downloaded_path):
            print(f"  ❌ File not found in staging: {final_downloaded_path}")
            continue

        # Task 2: The Swap
        if z_drive_path and z_drive_path != "Not Found in Z:/":
            try:
                os.makedirs(os.path.dirname(z_drive_path), exist_ok=True)
                shutil.move(final_downloaded_path, z_drive_path)
                final_path = z_drive_path
                print(f"  ✅ Track swapped successfully into Z:/")
            except Exception as e:
                print(f"  ❌ Failed to overwrite Z:/ path: {e}")
                continue
        else:
            print(f"  ⚠️ No Z:/ mapping found. Leaving file in staging.")
            final_path = final_downloaded_path

        # Task 3: Analysis
        print(f"  🔍 Starting Audio Analysis on: {os.path.basename(final_path)}")
        try:
            metrics = automaster._analyze_audio_properties(final_path)
            cue_in_ms = int(metrics.get('cue_in', 0))
            
            # Calculate new duration and length from mutagen
            try:
                from mutagen.mp3 import MP3
                audio = MP3(final_path)
                real_duration_ms = int(round(audio.info.length * 1000))
                real_length_str = f"{int(audio.info.length // 60)}:{int(audio.info.length % 60):02d}"
            except Exception as ex:
                real_duration_ms = 210000
                real_length_str = "3:30"

            # Update Master CSV data
            master_row_found = False
            for master_row in master_rows:
                if master_row.get('Track Name') == track_name:
                    master_row['Intro_Duration'] = cue_in_ms
                    master_row['Punch_Ms'] = cue_in_ms
                    master_row['Source_URL'] = search_query
                    # Update unified schema fields
                    if 'duration_ms' in master_row:
                        master_row['duration_ms'] = real_duration_ms
                    if 'Length' in master_row:
                        master_row['Length'] = real_length_str
                    master_row_found = True
                    break
            
            print(f"  ✅ Analysis Complete | Punch: {cue_in_ms}ms")
        except Exception as e:
            print(f"  ❌ Analysis Failed: {e}")
            continue

        success_count += 1
        time.sleep(2) # Throttling

    # Final Save
    if success_count > 0:
        with open(CSV_PATH, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=master_fieldnames)
            writer.writeheader()
            writer.writerows(master_rows)

    print(f"\nBatch Orchestrator Complete! Successfully processed {success_count} tracks.")

if __name__ == '__main__':
    main()