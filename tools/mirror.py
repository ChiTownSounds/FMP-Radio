import os
import sys
import subprocess
import time
import io
from pathlib import Path

# Set standard output encoding to UTF-8 to prevent console printing crashes on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MUSIC_DIR

# Source of Truth (Google Drive)
SOURCE_DIR = Path(MUSIC_DIR)
DESTINATION_REMOTE = "citrus3:"

# System folders that should be synchronized
ERA_FOLDERS = [
    "Classics", 
    "Old School 70s80s", 
    "Throwbacks 90s2000s", 
    "New School 2010+", 
    "Live", 
    "Unsorted_Review",
    "intro",
    "ondemand",
    "365 Commercials",
    "Shows"
]

def run_sync(live_mode=False):
    action_type = "LIVE SYNC" if live_mode else "DRY RUN"
    print("\n" + "="*70)
    print(f"FMP VAULT SYNC: REVERSED SOURCE-OF-TRUTH SYNC ({action_type})")
    print(f"Source:      {SOURCE_DIR} (G: Drive - Source of Truth)")
    print(f"Destination: {DESTINATION_REMOTE}/ (Citrus3 FTP - Mirror)")
    print("="*70 + "\n")

    # 1. CRITICAL MOUNT CHECK
    if not SOURCE_DIR.exists():
        print(f"[FATAL ERROR] Source path not found: {SOURCE_DIR}")
        print("Make sure music storage directory is active and reachable.")
        print("Aborting to prevent accidental deletion on remote server.")
        sys.exit(1)

    ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    def get_rclone_path():
        import platform
        import shutil
        if platform.system() == "Windows":
            path = os.path.join(ROOT_DIR, "rclone.exe")
            if os.path.exists(path):
                return path
        resolved = shutil.which("rclone")
        if resolved:
            return resolved
        return "rclone"

    rclone_path = get_rclone_path()

    # 2. Iterate through folders and run rclone sync
    for folder in ERA_FOLDERS:
        src_path = SOURCE_DIR / folder
        dest_path = f"{DESTINATION_REMOTE}/{folder}"

        # If local folder does not exist, create it to match system requirements
        if not src_path.exists():
            print(f"[INFO] Creating missing local folder: {folder}")
            src_path.mkdir(parents=True, exist_ok=True)

        print(f"[*] Syncing: /{folder} ...")

        # Build rclone command
        cmd = [
            rclone_path, "sync",
            str(src_path),
            dest_path,
            "--ignore-size",    # Handle FTP size padding discrepancies
            "--transfers", "4",  # Keep connections stable for FTP
            "--checkers", "8",
            "-P"                # Show real-time progress
        ]

        if not live_mode:
            cmd.append("--dry-run")

        try:
            # Run rclone and pipe output directly to stdout
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='ignore'
            )

            # Read and print output line by line as it runs
            for line in process.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()

            process.wait()
            if process.returncode == 0:
                print(f"[✓] Completed sync for /{folder}\n")
            else:
                print(f"[!] Warning: Rclone returned non-zero exit code {process.returncode} for /{folder}\n")
        except Exception as e:
            print(f"[ERROR] Failed to run sync for /{folder}: {e}\n")

    print("="*70)
    print(f"SYNC COMPLETE. Mode: {action_type}")
    print("="*70 + "\n")

if __name__ == "__main__":
    live_mode = False
    
    # Parse arguments
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg == "--live":
            live_mode = True
        elif arg == "--dry-run":
            live_mode = False
        else:
            print("Usage: python mirror.py [--live | --dry-run]")
            sys.exit(1)
    else:
        # Interactive Mode
        print("FMP VAULT SYNC SYSTEM")
        print("---------------------")
        print("This script synchronizes your local Google Drive (G:) to Citrus3 FTP.")
        print("Any files deleted on G: will be deleted from the FTP.")
        print("Any new files on G: will be uploaded to the FTP.")
        print("")
        user_input = input("Type 'LIVE' to execute the sync live, or press Enter to run a safe DRY RUN: ").strip().lower()
        if user_input == "live":
            live_mode = True

    start_time = time.time()
    run_sync(live_mode)
    elapsed = round(time.time() - start_time, 2)
    print(f"Time elapsed: {elapsed} seconds")