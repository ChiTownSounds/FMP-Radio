import os
import sys
import io
import csv
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TXXX, TBPM

# Ensure Windows terminal outputs are clean UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, "configs", "fmp_data_7718.csv")

def check_z_drive_accessible(path):
    """Fast-check if Z: drive is responsive without long blocks."""
    parent_dir = os.path.dirname(path)
    return os.path.exists(parent_dir)

def tag_single_track(row, dry_run=False):
    """Worker function to process and tag a single track in parallel."""
    file_path = row.get('File Path', '')
    if not file_path:
        return 'skipped', None

    norm_path = os.path.normpath(file_path)
    
    # Fast check for file existence
    if not os.path.exists(norm_path):
        return 'missing', row.get('Track Name')

    try:
        # Extract metrics from CSV row
        bpm = int(float(row.get('bpm', 0))) if row.get('bpm') else 0
        intro = int(float(row.get('Intro_Duration', 10000))) if row.get('Intro_Duration') else 10000
        punch = int(float(row.get('Punch_Ms', 0))) if row.get('Punch_Ms') else 0
        outro = int(float(row.get('outro_duration', 20000))) if row.get('outro_duration') else 20000

        # Read ID3 tags
        audio = MP3(norm_path)
        has_embedded_cue = False
        if audio.tags:
            has_embedded_cue = any(tag.desc.upper() == 'INTRO_DURATION' for tag in audio.tags.getall('TXXX'))
        
        if has_embedded_cue:
            return 'already_tagged', row.get('Track Name')

        # Embed cue points if missing
        if not dry_run:
            if audio.tags is None:
                audio.add_tags()
            
            # Add TXXX frames and TBPM
            audio.tags.add(TXXX(encoding=3, desc='INTRO_DURATION', text=[str(intro)]))
            audio.tags.add(TXXX(encoding=3, desc='PUNCH_MS', text=[str(punch)]))
            audio.tags.add(TXXX(encoding=3, desc='OUTRO_DURATION', text=[str(outro)]))
            audio.tags.add(TBPM(encoding=3, text=[str(bpm)]))
            audio.save()

        return 'success', row.get('Track Name')
    except Exception as e:
        return 'fail', f"{row.get('Track Name')} - Error: {e}"

def embed_cue_points(workers=30, dry_run=False):
    print("====================================================")
    print("   FMP PARALLEL CUE POINTS EMBEDDING ENGINE         ")
    print("====================================================")
    print(f"[*] Database Path: {CSV_PATH}")
    print(f"[*] Parallel Threads: {workers}")
    print(f"[*] Status: {'DRY RUN' if dry_run else 'LIVE RUN (Parallel MP3 Tagging)'}")
    
    if not os.path.exists(CSV_PATH):
        print(f"[-] Error: Master CSV not found at {CSV_PATH}")
        return

    # Read CSV rows
    rows = []
    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"[*] Loaded {len(rows)} records from master CSV.")
    print("[*] Verifying network storage access...")
    
    # Fast access test on the first file's folder to prevent blocking hang
    if rows and not check_z_drive_accessible(os.path.normpath(rows[0].get('File Path', ''))):
        print("[-] Fatal Error: Z: drive is offline. Please connect it in RaiDrive first!")
        return

    print(f"[*] Initializing parallel thread pool with {workers} workers...")

    success_count = 0
    already_tagged_count = 0
    missing_count = 0
    fail_count = 0

    # Thread Pool Execution with live updating tqdm progress bar
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(tag_single_track, row, dry_run): row for row in rows}
        
        with tqdm(total=len(rows), desc="Parallel Embedding ID3 Cue Points", unit="track") as pbar:
            for future in as_completed(futures):
                status, track_name = future.result()
                
                if status == 'success':
                    success_count += 1
                    tqdm.write(f"  [+] Tagged in parallel: '{track_name}'")
                elif status == 'already_tagged':
                    already_tagged_count += 1
                elif status == 'missing':
                    missing_count += 1
                elif status == 'fail':
                    fail_count += 1
                    tqdm.write(f"  [❌ FAIL] {track_name}")
                
                pbar.update(1)

    print("\n" + "="*50)
    print(" PARALLEL EMBEDDING SUMMARY:")
    print(f"   - Successfully Tagged:   {success_count}")
    print(f"   - Already Had ID3 Tags:  {already_tagged_count}")
    print(f"   - Missing Files:         {missing_count}")
    print(f"   - Failed / Errors:       {fail_count}")
    print("="*50 + "\n")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="FMP Parallel Metadata Tagger")
    parser.add_argument("--workers", type=int, default=30, help="Number of parallel workers (default: 30)")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing tags")
    args = parser.parse_args()

    embed_cue_points(workers=args.workers, dry_run=args.dry_run)
