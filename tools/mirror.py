import os
import shutil
import time
import sys
import io
from pathlib import Path

# Set standard output encoding to UTF-8 to prevent console printing crashes on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# THE CORRECTED PATHS
MASTER_DRIVE = Path("Z:/")
MIRROR_DRIVE = Path("G:/My Drive/FMP MUSIC/BASE/MUSIC")

ERA_FOLDERS = [
    "Classics", 
    "Old School 70s80s", 
    "Throwbacks 90s2000s", 
    "New School 2010+", 
    "Live", 
    "Unsorted_Review"
]

def strict_mirror():
    print("\n" + "="*50)
    print("FMP VAULT SYNC: STRICT ONE-WAY MIRROR (Z -> G)")
    print("="*50 + "\n")

    # CRITICAL MOUNT CHECK
    z_online = MASTER_DRIVE.exists() and os.path.exists(r"Z:/Classics")
    
    if not z_online:
        print("[WARNING] Master Drive Z: is offline. Operating in direct Rclone command-line fallback mode.")
    if not MIRROR_DRIVE.exists():
        print(f"[FATAL] Mirror path not found: {MIRROR_DRIVE}")
        print("Make sure Google Drive is running and you can see this folder in Explorer.")
        return

    import subprocess
    total_deleted = 0
    total_copied = 0

    for folder in ERA_FOLDERS:
        master_dir = MASTER_DRIVE / folder
        mirror_dir = MIRROR_DRIVE / folder

        # VERIFICATION LOCK: Never create, only sync existing.
        if not mirror_dir.exists():
            print(f"[ERROR] Target folder missing on G: {folder}. Skipping to protect structure.")
            continue

        if z_online and not master_dir.exists():
            print(f"[SKIP] Source folder missing on Z: {folder}")
            continue

        print(f"Scanning: [{folder}]...")

        # Get mirror files list
        mirror_files = {f.name: f.stat().st_size for f in mirror_dir.glob("*.mp3")}

        # Get master files list (from Z: or remote Citrus3 via rclone)
        if z_online:
            master_files = {f.name: f.stat().st_size for f in master_dir.glob("*.mp3")}
        else:
            # Fallback to direct rclone lsf format sp
            master_files = {}
            rclone_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rclone.exe")
            cmd = [rclone_path, "lsf", f"citrus3:/{folder}", "--format", "sp", "--files-only", "--separator", ";"]
            res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
            if res.returncode == 0:
                for line in res.stdout.split('\n'):
                    line = line.strip()
                    if ";" in line and line.lower().endswith(".mp3"):
                        parts = line.split(";", 1)
                        try:
                            size = int(parts[0])
                            name = parts[1]
                            master_files[name] = size
                        except:
                            pass

        # STEP 1: The Purge (Z/Citrus3 is the Boss. If it's not on Z/Citrus3, it dies on G)
        for m_file_name in list(mirror_files.keys()):
            if m_file_name not in master_files:
                target_path = mirror_dir / m_file_name
                try:
                    target_path.unlink()
                    print(f"  [-] DELETED from Mirror: {m_file_name}")
                    total_deleted += 1
                except Exception as e:
                    print(f"  [ERROR] Could not delete {m_file_name}: {e}")

        # STEP 2: The Sync (Copy missing or changed files)
        for z_file_name, z_size in master_files.items():
            source_path = master_dir / z_file_name
            target_path = mirror_dir / z_file_name
            
            needs_copy = False
            action_text = ""

            if z_file_name not in mirror_files:
                needs_copy = True
                action_text = "COPIED (New arrival)"
            elif z_size != mirror_files[z_file_name]:
                needs_copy = True
                action_text = "OVERWRITTEN (Upgraded master)"

            if needs_copy:
                print(f"  [+] SYNCING: {z_file_name} -> {action_text}")
                try:
                    if z_online:
                        shutil.copy2(source_path, target_path)
                    else:
                        # Direct rclone download fallback
                        rclone_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rclone.exe")
                        remote_src = f"citrus3:/{folder}/{z_file_name}"
                        subprocess.run([rclone_path, "copyto", remote_src, str(target_path)], check=True)
                    total_copied += 1
                except Exception as e:
                    print(f"  [ERROR] Failed to copy {z_file_name}: {e}")

    print("\n" + "="*50)
    print(f"SYNC COMPLETE. Deleted: {total_deleted} | Copied: {total_copied}")
    print("="*50 + "\n")

if __name__ == "__main__":
    start_time = time.time()
    strict_mirror()
    elapsed = round(time.time() - start_time, 2)
    print(f"Time elapsed: {elapsed} seconds")