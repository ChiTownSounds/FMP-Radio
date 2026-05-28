import csv
import os
import subprocess
import sys

def find_file_on_z(track_name):
    print(f"Searching Z:\\ recursively for '{track_name}'...")
    target_filename = f"{track_name}.mp3".lower()
    
    # Walk Z:\ and search for target_filename
    for root, dirs, files in os.walk("Z:\\"):
        for file in files:
            if file.lower() == target_filename:
                path = os.path.join(root, file)
                print(f"Found matching track at: {path}")
                return path
    return None

def main():
    csv_path = os.path.join("configs", "fmp_data_7718.csv")
    if not os.path.exists(csv_path):
        print(f"Error: CSV file not found at {csv_path}")
        sys.exit(1)
        
    print(f"Reading CSV from {csv_path}...")
    track_name = None
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('Track Name'):
                track_name = row['Track Name'].strip()
                break
                
    if not track_name:
        print("Error: No track found in CSV.")
        sys.exit(1)
        
    print(f"Target Track Name from CSV: '{track_name}'")
    
    # Find on Z:\
    target_file_path = find_file_on_z(track_name)
    if not target_file_path:
        print(f"Error: Could not locate '{track_name}.mp3' on Z:\\")
        sys.exit(1)
        
    # Construct exact FFmpeg command
    command = [
        'ffmpeg', '-hide_banner',
        '-i', target_file_path,
        '-af', 'silencedetect=noise=-48dB:duration=2.0',
        '-f', 'null', '-'
    ]
    
    print("\n================================================================================")
    print(f"EXECUTING SUBPROCESS COMMAND:")
    print(" ".join(f'"{arg}"' if ' ' in arg else arg for arg in command))
    print("================================================================================\n")
    
    # Bypassing try/except wrappers completely as requested. 
    # Any access violation or subprocess crash will raise a hard error.
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='ignore', check=True)
    
    print("\n========================= RAW FFMPEG STDERR OUTPUT =========================")
    print(result.stderr)
    print("============================================================================\n")

if __name__ == '__main__':
    main()
