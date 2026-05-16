import os
import shutil
import time
from pathlib import Path

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
    if not MASTER_DRIVE.exists():
        print(f"[FATAL] Master Drive {MASTER_DRIVE} is offline. Check RaiDrive.")
        return
    if not MIRROR_DRIVE.exists():
        print(f"[FATAL] Mirror path not found: {MIRROR_DRIVE}")
        print("Make sure Google Drive is running and you can see this folder in Explorer.")
        return

    total_deleted = 0
    total_copied = 0

    for folder in ERA_FOLDERS:
        master_dir = MASTER_DRIVE / folder
        mirror_dir = MIRROR_DRIVE / folder

        # VERIFICATION LOCK: Never create, only sync existing.
        if not mirror_dir.exists():
            print(f"[ERROR] Target folder missing on G: {folder}. Skipping to protect structure.")
            continue

        if not master_dir.exists():
            print(f"[SKIP] Source folder missing on Z: {folder}")
            continue

        print(f"Scanning: [{folder}]...")

        # Get file lists and sizes
        master_files = {f.name: f.stat().st_size for f in master_dir.glob("*.mp3")}
        mirror_files = {f.name: f.stat().st_size for f in mirror_dir.glob("*.mp3")}

        # STEP 1: The Purge (Z is the Boss. If it's not on Z, it dies on G)
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
                    shutil.copy2(source_path, target_path)
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