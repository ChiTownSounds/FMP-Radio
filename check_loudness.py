import os
import csv
import re
import random
import subprocess
import shutil
from datetime import datetime
from ftplib import FTP
from typing import List, Tuple

# Import credentials from the central config
from config import FTP_HOST, FTP_PORT, FTP_USER, FTP_PASS, FTP_BASE_DIR

# ==============================================================================
# FMP ULTIMATE - LOUDNESS AUDIT UTILITY (V3 PRECISION)
# ==============================================================================
# MISSION: 
# Connect to Citrus3 FTP, sample music files, and analyze Integrated LUFS.
# Hardened to ignore initial -70.0 baseline and capture the FINAL summary value.
# ==============================================================================

SAMPLE_LIMIT = 20
TEMP_DIR = r"C:\FMP_Ultimate\staging\_loudness_temp"
REPORT_PATH = r"C:\FMP_Ultimate\loudness_analysis.csv"
ERA_FOLDERS = ["Classics", "Old School 70s80s", "Throwbacks 90s2000s", "New School 2010+", "Live"]

def analyze_lufs(file_path: str) -> float:
    """
    Executes FFmpeg with the EBUR128 filter. 
    Captures the FINAL Summary Integrated LUFS, avoiding the -70.0 initial frame.
    """
    try:
        cmd = ["ffmpeg", "-i", file_path, "-af", "ebur128", "-f", "null", "-"]
        
        process = subprocess.run(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True, 
            encoding='utf-8', 
            errors='ignore',
            timeout=120
        )
        
        # [PRECISION PARSING]
        # FFmpeg's ebur128 filter prints real-time updates starting at -70.0 LUFS.
        # We must ignore all intermediate frames and capture the very last instance
        # found in the "Summary" block.
        matches = re.findall(r"I:\s+([-\d.]+)\s+LUFS", process.stderr)
        
        if matches:
            final_lufs = float(matches[-1])
            # Basic validation: ensure it's not the dummy baseline
            if final_lufs == -70.0:
                print(f"    !! Warning: Capture returned -70.0 baseline for {os.path.basename(file_path)}.")
            return final_lufs
            
        print(f"    !! Error: No LUFS patterns detected in FFmpeg output.")
    except Exception as e:
        print(f"    !! Analysis Failed: {e}")
    return None

def run_audit():
    print("="*80)
    print(" FMP ULTIMATE - LOUDNESS AUDIT ENGINE (V3 PRECISION)")
    print("="*80)
    print(f" Target Sample Size: {SAMPLE_LIMIT}")
    print(f" Temporary Staging:  {TEMP_DIR}")
    print("="*80)

    os.makedirs(TEMP_DIR, exist_ok=True)
    results = []

    try:
        ftp = FTP()
        print(f"Connecting to {FTP_HOST}:{FTP_PORT}...")
        ftp.connect(FTP_HOST, FTP_PORT, timeout=15)
        ftp.login(FTP_USER, FTP_PASS)
        ftp.set_pasv(True)

        # 1. Collect potential candidates from all era folders
        print("Gathering remote file list...")
        all_candidates = []
        for folder in ERA_FOLDERS:
            try:
                ftp.cwd(f"/{folder}")
                files = [f for f in ftp.nlst() if f.lower().endswith(".mp3")]
                for f in files:
                    all_candidates.append((folder, f))
            except Exception as e:
                print(f"  !! Skipping folder /{folder}: {e}")

        if not all_candidates:
            print("!! Error: No MP3 files found on server.")
            return

        # 2. Randomize and limit sample
        sample_pool = random.sample(all_candidates, min(len(all_candidates), SAMPLE_LIMIT))
        print(f"Successfully sampled {len(sample_pool)} tracks for analysis.\n")

        # 3. Process one-by-one (download -> analyze -> delete)
        for idx, (folder, filename) in enumerate(sample_pool):
            local_path = os.path.join(TEMP_DIR, filename)
            print(f"[{idx+1}/{len(sample_pool)}] Analyzing: {filename}")
            
            try:
                # Download
                ftp.cwd(f"/{folder}")
                with open(local_path, 'wb') as f:
                    ftp.retrbinary(f"RETR {filename}", f.write)
                
                # Analyze
                lufs = analyze_lufs(local_path)
                
                if lufs is not None:
                    print(f"    > Measured: {lufs} LUFS")
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    results.append(lufs)
                    
                    # Log to CSV
                    file_exists = os.path.exists(REPORT_PATH)
                    with open(REPORT_PATH, 'a', encoding='utf-8', newline='') as f:
                        writer = csv.writer(f)
                        if not file_exists:
                            writer.writerow(["Timestamp", "Folder", "Filename", "Measured_LUFS"])
                        writer.writerow([timestamp, folder, filename, lufs])
                else:
                    print(f"    !! Data Parsing Failure for: {filename}")

            except Exception as e:
                print(f"    !! Download/Process Failure: {e}")
            finally:
                # Immediate cleanup: ensure only one file exists at a time
                if os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                    except:
                        pass

        ftp.quit()

        # 4. Final Summary Report
        if results:
            print("\n" + "="*80)
            print(" LOUDNESS AUDIT SUMMARY")
            print("-" * 80)
            print(f" Highest Loudness: {max(results):.2f} LUFS")
            print(f" Lowest Loudness:  {min(results):.2f} LUFS")
            print(f" Average Loudness: {sum(results)/len(results):.2f} LUFS")
            print("-" * 80)
            print(f" Report saved to: {REPORT_PATH}")
            print("="*80 + "\n")
        else:
            print("\n!! Audit completed with no valid data captured.")

    except Exception as e:
        print(f"!! Critical Engine Failure: {e}")
    finally:
        # Final cleanup of temp directory
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR, ignore_errors=True)

if __name__ == "__main__":
    run_audit()
