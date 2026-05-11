import os
import subprocess
from pathlib import Path

# TARGET FOLDER ON GOOGLE DRIVE
AUDIT_PATH = Path(r"G:\My Drive\FMP MUSIC\BASE\MUSIC")
REPORT_FILE = "fmp_audit_hitlist.txt"

ERA_FOLDERS = [
    "Classics", "Old School 70s80s", "Throwbacks 90s2000s", 
    "New School 2010+", "Live", "Unsorted_Review"
]

# TIGHTER THRESHOLD: -45.0 is much pickier than -55.0. 
# It requires more high-frequency energy to pass.
THRESHOLD = -45.0

def analyze_frequency(file_path):
    try:
        # 1. Get Duration
        cmd_dur = [
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration', 
            '-of', 'default=noprint_wrappers=1:nokey=1', str(file_path)
        ]
        result_dur = subprocess.run(cmd_dur, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        
        if not result_dur.stdout.strip():
            return None, "Duration Error"
            
        duration = float(result_dur.stdout.strip())
        # Seek further in to avoid intro sound effects/dialogue
        seek_time = str(int(duration * 0.4)) 

        # 2. High-Pass Analysis
        # We isolate everything ABOVE 16,000Hz and measure its volume.
        test_cmd = [
            'ffmpeg', '-ss', seek_time, '-t', '10', '-i', str(file_path),
            '-af', 'highpass=f=16000,volumedetect', '-f', 'null', '-'
        ]
        
        output = subprocess.run(test_cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore').stderr
        
        if "max_volume:" in output:
            max_vol = float(output.split("max_volume:")[1].split("dB")[0].strip())
            
            # If energy above 16k is quieter than our threshold, it's a fake.
            is_fake = max_vol < THRESHOLD
            return is_fake, max_vol
            
    except Exception as e:
        return None, str(e)
    return False, 0

def run_audit():
    print("\n" + "="*60)
    print("FMP FORENSIC AUDIT: AGGRESSIVE MODE")
    print(f"Threshold: {THRESHOLD}dB (Lower is quieter/faker)")
    print("="*60 + "\n")

    if not AUDIT_PATH.exists():
        print(f"[ERROR] Cannot find {AUDIT_PATH}")
        return

    hitlist = []
    processed = 0

    for folder in ERA_FOLDERS:
        current_dir = AUDIT_PATH / folder
        if not current_dir.exists(): continue
        
        print(f"\n--- Scanning {folder} ---")
        files = list(current_dir.glob("*.mp3"))
        
        for file in files:
            processed += 1
            is_fake, val = analyze_frequency(file)
            
            status = "PASS"
            if is_fake is True:
                status = "!!!! FLAG !!!!"
                hitlist.append(f"{folder} | {file.name} | {val}dB")
            elif is_fake is None:
                status = "ERROR"

            # This gives you the live telemetry so you can see the math working
            print(f"[{processed}] {val}dB | {status} | {file.name[:50]}")

    with open(REPORT_FILE, "w", encoding='utf-8') as f:
        f.write(f"FMP AUDIT HITLIST (Threshold: {THRESHOLD}dB)\n")
        f.write("================================================\n")
        if hitlist:
            f.write("\n".join(hitlist))
        else:
            f.write("No fakes found at this threshold.")

    print("\n" + "="*60)
    print(f"AUDIT COMPLETE. Flagged: {len(hitlist)} / {processed}")
    print(f"Check {REPORT_FILE} for the full list.")
    print("="*60 + "\n")

if __name__ == "__main__":
    run_audit()