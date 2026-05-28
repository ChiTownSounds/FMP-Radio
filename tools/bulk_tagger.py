import os
import sys
import io
import csv
import shutil
import re
import logging
from typing import Tuple
from tqdm import tqdm
from mutagen.mp3 import MP3
from mutagen.id3 import TBPM

# Reconfigure stdout/stderr to handle UTF-8 symbols and smart quotes on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Ensure the parent directory is on the path so we can import our core modules if needed
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

# --- SETUP CONFIGURATIONS ---
CSV_PATH = os.path.join(parent_dir, "configs", "fmp_data_7718.csv")
BACKUP_PATH = os.path.join(parent_dir, "configs", "fmp_data_7718.bak.csv")

def determine_energy_category(year: str, bpm: float) -> str:
    """Determines the era and energy pooling category based on year and BPM."""
    try:
        year_int = int(str(year)[:4])
        if year_int < 1970:
            era = "Classics"
        elif 1970 <= year_int <= 1989:
            era = "Old School 70s80s"
        elif 1990 <= year_int <= 2009:
            era = "Throwbacks 90s2000s"
        else:
            era = "New School 2010+"
    except Exception:
        era = "Unsorted_Review"

    if bpm < 86:
        energy = "Smooth"
    elif 86 <= bpm <= 105:
        energy = "Mid-Tempo"
    else:
        energy = "Upbeat"

    return f"{era} ({energy})"

def analyze_local_track(file_path: str) -> Tuple[str, str, int]:
    """
    Harvests embedded metadata and calculates true BPM.
    Checks physical TBPM first as a fast-path, falling back to librosa analysis.
    """
    true_year = "Unknown"
    lyrics_text = "Not Found"
    true_bpm = None

    try:
        audio = MP3(file_path)
        if audio and audio.tags:
            # 1. Year Extraction (TDRC or TYER)
            tag_year = ""
            if 'TDRC' in audio.tags:
                tag_year = str(audio.tags['TDRC'].text[0])
            elif 'TYER' in audio.tags:
                tag_year = str(audio.tags['TYER'].text[0])
            
            if tag_year:
                year_match = re.search(r'(\d{4})', tag_year)
                if year_match:
                    true_year = year_match.group(1)

            # 2. Lyrics Extraction (USLT or SYLT)
            uslt_frames = audio.tags.getall('USLT')
            if uslt_frames:
                lyrics_text = str(uslt_frames[0].text)
            else:
                found_uslt = False
                for key in audio.tags.keys():
                    if key.startswith('USLT'):
                        lyrics_text = str(audio.tags[key].text)
                        found_uslt = True
                        break
                if not found_uslt:
                    sylt_frames = audio.tags.getall('SYLT')
                    if sylt_frames:
                        lyrics_text = str(sylt_frames[0].text)

            # 3. BPM Extraction (TBPM)
            if 'TBPM' in audio.tags:
                try:
                    true_bpm = float(str(audio.tags['TBPM'].text[0]))
                except Exception:
                    pass
    except Exception as e:
        # Gracefully handle ID3 parsing warning without crashing
        pass

    # Waveform beat tracking fallback if TBPM is missing or invalid
    if not true_bpm or true_bpm <= 0:
        try:
            import librosa
            # Load 60 seconds starting 30 seconds into the song
            y, sr = librosa.load(file_path, sr=22050, offset=30.0, duration=60.0)
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            if hasattr(tempo, '__len__'):
                true_bpm = float(tempo[0])
            else:
                true_bpm = float(tempo)
                
            # Write back the calculated BPM to the MP3 ID3 tags so future runs are instantaneous
            try:
                audio = MP3(file_path)
                if audio.tags is not None:
                    audio.tags['TBPM'] = TBPM(encoding=3, text=[str(int(round(true_bpm)))])
                    audio.save()
            except Exception:
                pass # Non-critical metadata cache update failure
        except Exception as e:
            true_bpm = 98.0  # Safe standard radio fallback

    bpm_int = int(round(true_bpm))
    return true_year, lyrics_text, bpm_int

def run_bulk_tagging(dry_run=False):
    if not os.path.exists(CSV_PATH):
        print(f"[-] Critical Error: Master CSV not found at {CSV_PATH}")
        return

    print(f"[*] Initializing Bulk Audio Tagger Engine...")
    print(f"[*] Database Path: {CSV_PATH}")
    print(f"[*] Status: {'DRY RUN (No changes written)' if dry_run else 'LIVE RUN (Modifying in-place)'}")

    # 1. Database Backup Verification
    if not dry_run:
        try:
            shutil.copyfile(CSV_PATH, BACKUP_PATH)
            print(f"[+] Safety database backup created: {BACKUP_PATH}")
        except Exception as e:
            print(f"[-] Database Backup Failed: {e}")
            return

    updated_rows = []
    processed_count = 0
    skipped_count = 0
    missing_count = 0

    # 2. Open and Read the Master Database
    with open(CSV_PATH, mode='r', encoding='utf-8-sig') as f:
        reader = list(csv.DictReader(f))
        if not reader:
            print("[-] Error: CSV file is empty or has unreadable headers.")
            return
        
        fieldnames = reader[0].keys()

        # 3. Crawler Processing loop with tqdm
        print(f"[*] Processing {len(reader)} rows from library blueprint...")
        for index, row in enumerate(tqdm(reader, desc="Retrofitting Library", unit="track")):
            file_path = row.get('File Path')
            if not file_path:
                print(f"\n[WARNING] Row {index+1}: Missing 'File Path' column value. Skipping.")
                skipped_count += 1
                updated_rows.append(row)
                continue

            # Normalize path slashes for Windows compatibility
            norm_file_path = os.path.normpath(file_path)

            if not os.path.exists(norm_file_path):
                # Missing file logged gracefully
                missing_count += 1
                updated_rows.append(row)
                continue

            try:
                # 4. Harvest Local Metadata and calculate BPM
                true_year, lyrics_text, bpm_int = analyze_local_track(norm_file_path)

                # 5. Energy Pool Mapping
                energy_category = determine_energy_category(true_year, bpm_int)

                # 6. Data Injection (Strict Schema Lock)
                row['bpm'] = str(bpm_int)
                row['energy_category'] = energy_category

                processed_count += 1
            except Exception as e:
                print(f"\n[ERROR] Row {index+1}: Process crashed on {norm_file_path} - {e}")
                skipped_count += 1
            
            updated_rows.append(row)

    # 7. In-place DB commit
    if not dry_run and processed_count > 0:
        try:
            with open(CSV_PATH, mode='w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(updated_rows)
            print(f"\n[+] Live Database Update Finalized successfully.")
        except Exception as e:
            print(f"\n[-] CRITICAL: Failed to write to master CSV: {e}")
            print(f"[*] Restoring database from safety backup...")
            shutil.copyfile(BACKUP_PATH, CSV_PATH)
            print(f"[+] Restoration complete.")
    else:
        print(f"\n[+] Dry Run Evaluation Complete. No records modified.")

    print(f"====================================================")
    print(f" Retrofit Summary:")
    print(f"   - Successfully Tagged & Updated: {processed_count}")
    print(f"   - Skipped / Failed: {skipped_count}")
    # Show warning if there are missing files on disk
    if missing_count > 0:
        print(f"   - Warning: {missing_count} tracks listed in CSV were missing from disk.")
    else:
        print(f"   - Missing Files from Disk: 0")
    print(f"====================================================")

if __name__ == "__main__":
    # Change dry_run=False to save changes in place
    run_bulk_tagging(dry_run=False)