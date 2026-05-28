import os
import sys
import io
import time
import subprocess
import shutil

# Ensure Windows terminal outputs are clean UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RCLONE_PATH = os.path.join(BASE_DIR, "rclone.exe")
DROPZONE_DIR = os.path.join(BASE_DIR, "dropzone")
COMPLETED_DIR = os.path.join(DROPZONE_DIR, "completed")

def wait_for_file_stable(path, check_interval=2, timeout=60):
    """Waits until the file size stops changing (meaning download/copy is complete)."""
    last_size = -1
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            current_size = os.path.getsize(path)
            if current_size == last_size and current_size > 0:
                return True
            last_size = current_size
        except:
            pass
        time.sleep(check_interval)
        
    return False

def watch_dropzone():
    print("====================================================")
    print("      FMP HIGH-SPEED HOT FOLDER WATCHER             ")
    print("====================================================")
    print(f"[*] Watching Folder: {DROPZONE_DIR}")
    print(f"[*] Completed Folder: {COMPLETED_DIR}")
    
    # Ensure directories exist
    os.makedirs(DROPZONE_DIR, exist_ok=True)
    os.makedirs(COMPLETED_DIR, exist_ok=True)

    if not os.path.exists(RCLONE_PATH):
        print(f"[-] Error: rclone.exe not found at {RCLONE_PATH}")
        return

    print("[*] Active and listening for new MP3 files. Drop files to upload instantly...")

    while True:
        try:
            # List files in dropzone (excluding directories)
            files = [f for f in os.listdir(DROPZONE_DIR) if os.path.isfile(os.path.join(DROPZONE_DIR, f))]
            mp3_files = [f for f in files if f.lower().endswith('.mp3')]

            for file in mp3_files:
                local_path = os.path.join(DROPZONE_DIR, file)
                print(f"\n[+] New track detected: '{file}'")
                print("[*] Waiting for file download/copy transfer to complete...")
                
                if wait_for_file_stable(local_path):
                    print(f"[*] File is stable. Launching Rclone multi-threaded upload for '{file}'...")
                    
                    # Rclone command to copy the file directly to Citrus3 /Unsorted_Review
                    # Using parallel acceleration transfers
                    rclone_cmd = [
                        RCLONE_PATH, "copyto", local_path, f"citrus3:/Unsorted_Review/{file}",
                        "--transfers", "8",
                        "--checkers", "16",
                        "--stats", "1s"
                    ]
                    
                    try:
                        # Run the upload
                        subprocess.run(rclone_cmd, check=True, capture_output=True)
                        print(f"[OK] Upload Successful! '{file}' has been secured in Citrus3.")
                        
                        # Move to completed folder to clear the dropzone
                        dest_path = os.path.join(COMPLETED_DIR, file)
                        shutil.move(local_path, dest_path)
                        print(f"[*] Moved '{file}' locally to completed archive.")
                    except subprocess.CalledProcessError as e:
                        print(f"[-] Rclone Upload Failed: {e.stderr.decode('utf-8', errors='ignore')}")
                else:
                    print(f"[-] Timeout: File '{file}' did not stabilize. Skipping.")

        except Exception as e:
            print(f"[-] Monitor loop encountered an error: {e}")

        # Poll every 2 seconds
        time.sleep(2)

if __name__ == '__main__':
    watch_dropzone()
