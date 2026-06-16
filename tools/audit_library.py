import os
import sys
import csv
import threading

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CSV_BLUEPRINT, FTP_HOST, FTP_PORT, FTP_USER, FTP_PASS, FTP_BASE_DIR

# ==============================================================================
# FMP ULTIMATE - STANDALONE MAINTENANCE UTILITY
# ==============================================================================
# MISSION: 
# 1. Deduplicate the local CSV database.
# 2. Realign "Live" tracks found in historical era folders to the /Live dir.
# 3. Identify low-quality tracks for high-fidelity replacement.
# ==============================================================================

DRY_RUN = True  # Set to False to commit changes to CSV and FTP server

def audit_local_csv():
    print("\n[PHASE 1] Scanning Local Database for Duplicates...")
    if not os.path.exists(CSV_BLUEPRINT):
        print("!! Error: CSV_BLUEPRINT not found.")
        return 0, 0

    seen_tracks = set()
    clean_rows = []
    duplicate_count = 0
    total_original = 0

    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            total_original += 1
            name = row.get('Track Name', '').strip()
            if name in seen_tracks:
                duplicate_count += 1
                if DRY_RUN:
                    print(f"  > [FLAGGED DUPE] {name}")
            else:
                seen_tracks.add(name)
                clean_rows.append(row)

    if not DRY_RUN and duplicate_count > 0:
        print(f"\n[ACTION] Writing {len(clean_rows)} unique records to database...")
        try:
            with open(CSV_BLUEPRINT, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(clean_rows)
            print("  > Database write complete.")
        except Exception as e:
            print(f"  !! Failed to write CSV: {e}")
    
    return total_original, duplicate_count

def audit_remote_files():
    print("\n[PHASE 2] Scanning Remote for Misplaced Live Tracks via Rclone...")
    era_folders = ["Classics", "Old School 70s80s", "Throwbacks 90s2000s", "New School 2010+", "Unsorted_Review"]
    live_found_count = 0
    move_success_count = 0
    import subprocess
    rclone_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rclone.exe")

    try:
        if not DRY_RUN:
            subprocess.run([rclone_path, "mkdir", "citrus3:/Live"], capture_output=True)
            
        for era in era_folders:
            remote_path = f"citrus3:/{era}"
            try:
                result = subprocess.run([rclone_path, "lsf", remote_path], capture_output=True, text=True, check=True, encoding='utf-8')
                files = [line.strip() for line in result.stdout.split('\n') if line.strip()]
                for filename in files:
                    if "live" in filename.lower() and filename.endswith(".mp3"):
                        live_found_count += 1
                        old_path = f"citrus3:/{era}/{filename}"
                        new_path = f"citrus3:/Live/{filename}"
                        
                        if DRY_RUN:
                            print(f"  > [FLAGGED LIVE] {old_path} -> {new_path}")
                        else:
                            try:
                                print(f"  > [MOVING] {filename}...")
                                subprocess.run([rclone_path, "moveto", old_path, new_path], check=True, capture_output=True)
                                move_success_count += 1
                            except subprocess.CalledProcessError as e:
                                print(f"    !! Move Failed: {e.stderr}")
            except subprocess.CalledProcessError as e:
                print(f"  !! Could not scan /{era}: {e.stderr}")
                continue
    except Exception as e:
        print(f"!! Rclone Connection Error: {e}")

    return live_found_count, move_success_count

def audit_quality_upgrades():
    """
    [PHASE 3] UPGRADE SCANNER
    Identifies tracks with low bitrate, VBR, or fake transcodes.
    """
    print("\n[PHASE 3] Scanning for Low-Quality Tracks (Upgrade Queue)...")
    upgrade_list = []
    
    if not os.path.exists(CSV_BLUEPRINT):
        return 0

    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            bitrate_str = row.get('Bitrate', '0').lower()
            track_name = row.get('Track Name', 'Unknown')
            
            is_low_quality = False
            # Check for keywords
            if "vbr" in bitrate_str or "fake" in bitrate_str:
                is_low_quality = True
            else:
                # Extract numeric bitrate
                try:
                    num_bitrate = int(''.join(filter(str.isdigit, bitrate_str)))
                    if 0 < num_bitrate < 320:
                        is_low_quality = True
                except:
                    pass
            
            if is_low_quality:
                upgrade_list.append(track_name)
                print(f"  > [FLAGGED UPGRADE] {track_name} (Current: {bitrate_str})")

    if not DRY_RUN and upgrade_list:
        queue_path = r"C:\FMP_Ultimate\upgrade_queue.txt"
        try:
            with open(queue_path, 'w', encoding='utf-8') as f:
                for track in upgrade_list:
                    f.write(f"{track}\n")
            print(f"  > Upgrade queue written to {queue_path}")
        except Exception as e:
            print(f"  !! Failed to write upgrade queue: {e}")
            
    return len(upgrade_list)

def audit_missing_tracks():
    """
    [PHASE 4] SYNCHRONIZATION SCANNER
    Identifies tracks on the remote server that are missing from the local CSV database.
    """
    print("\n[PHASE 4] Scanning Remote Server for Untracked Files...")
    
    if not os.path.exists(CSV_BLUEPRINT):
        return 0
    
    # 1. Load all known paths and keys from CSV
    from modules.storage import VaultManager
    from config import is_non_song
    vm = VaultManager()
    
    known_paths = set()
    known_keys = set()
    fieldnames = []
    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            # Normalize to forward slashes just in case
            path = row.get('File Path', '').replace('\\', '/').lower()
            if path.startswith('z:/'):
                path = path[3:]
            known_paths.add(path)
            
            track_name = row.get('Track Name', '')
            if track_name:
                key = vm._normalize_track_key(track_name)
                if key:
                    known_keys.add(key)
            
    # 2. Get all remote files via rclone
    import subprocess
    import platform
    import shutil
    
    def get_rclone_path():
        if platform.system() == "Windows":
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rclone.exe")
            if os.path.exists(path):
                return path
        resolved = shutil.which("rclone")
        if resolved:
            return resolved
        return "rclone"

    rclone_path = get_rclone_path()
    remote_files = []
    try:
        # Use rclone lsf with recursive
        result = subprocess.run([rclone_path, "lsf", "citrus3:/", "--recursive"], capture_output=True, text=True, check=True, encoding='utf-8')
        remote_files = [line.strip() for line in result.stdout.split('\n') if line.strip().lower().endswith('.mp3')]
    except subprocess.CalledProcessError as e:
        print(f"  !! Rclone Sync Error: {e.stderr}")
        return 0
        
    # 3. Find missing files
    missing_files = []
    for r_file in remote_files:
        # r_file format: e.g. "Throwbacks 90s2000s/Artist - Song.mp3"
        normalized_remote = r_file.replace('\\', '/').lower()
        if normalized_remote in known_paths:
            continue
            
        # Skip duplicate music files if already tracked
        basename = os.path.basename(r_file)
        filename_no_ext = os.path.splitext(basename)[0]
        
        if is_non_song(filename_no_ext, r_file):
            missing_files.append(r_file)
            continue
            
        key = vm._normalize_track_key(filename_no_ext)
        if key in known_keys:
            # Already tracked in database under a cleaner name/path
            continue
            
        missing_files.append(r_file)
            
    # 4. Generate Text Report
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    report_path = os.path.join(repo_dir, "missing_tracks_report.txt")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"--- FMP Ultimate: Missing Tracks Report ---\n")
            f.write(f"Total Untracked Files Found: {len(missing_files)}\n\n")
            for mf in missing_files:
                f.write(f"{mf}\n")
        print(f"  > Generated report of {len(missing_files)} missing tracks at: {report_path}")
    except Exception as e:
        print(f"  !! Failed to write report: {e}")
        
    if not missing_files:
        print("  > Database is fully synchronized with the remote server.")
        return 0
        
    # 5. Inject into CSV
    if not DRY_RUN:
        print(f"\n[ACTION] Injecting {len(missing_files)} missing tracks into local CSV database...")
        new_rows = []
        for mf in missing_files:
            folder_name = mf.split('/')[0] if '/' in mf else 'Unsorted_Review'
            track_name = mf.split('/')[-1].replace('.mp3', '')
            
            clean_cat = "Throwbacks"
            folder_lower = folder_name.lower()
            if "classics" in folder_lower: clean_cat = "Classics"
            elif "old school" in folder_lower: clean_cat = "Old School"
            elif "throwbacks" in folder_lower: clean_cat = "Throwbacks"
            elif "new school" in folder_lower: clean_cat = "New School"
            
            new_row = {
                'Track Name': track_name,
                'File Path': f"Z:/{mf}",
                'Source_URL': '',
                'duration_ms': '0',
                'item_type': 'Music',
                'energy_category': clean_cat,
                'Intro_Duration': '0',
                'Punch_Ms': '0',
                'outro_duration': '0',
                'bpm': '100',
                'Bitrate': 'Unknown',
                'Lyrics': 'Unknown',
                'Year': 'Unknown',
                'Art Ratio': '1.0',
                'Length': '0:00',
                'Pool': '3'
            }
            new_rows.append(new_row)
            
        try:
            with open(CSV_BLUEPRINT, 'a', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writerows(new_rows)
            print("  > Database injection complete.")
        except Exception as e:
            print(f"  !! Failed to inject CSV: {e}")
    else:
        print(f"  > [DRY RUN] {len(missing_files)} tracks would be injected into the CSV.")
        
    return len(missing_files)

def run_audit():
    print("="*80)
    print(" FMP ULTIMATE - LIBRARY AUDIT & REALIGNMENT UTILITY")
    print("="*80)
    if DRY_RUN:
        print(" !!! RUNNING IN DRY_RUN MODE - NO CHANGES WILL BE MADE !!!")
    else:
        print(" !!! LIVE MODE - COMMITTING CHANGES !!!")
    print("="*80)

    total, dupes = audit_local_csv()
    found_live, moved_live = audit_remote_files()
    to_upgrade = audit_quality_upgrades()
    synced_missing = audit_missing_tracks()

    print("\n" + "="*80)
    print(" AUDIT SUMMARY REPORT")
    print("-"*80)
    print(f" Local Database Scan:")
    print(f"   - Total Rows Analyzed: {total}")
    print(f"   - Duplicates Found:    {dupes}")
    print(f"   - Action:              {'Reported Only' if DRY_RUN else 'Database Cleaned'}")
    print(f"\n Remote FTP Scan:")
    print(f"   - Live Tracks Misfiled: {found_live}")
    if not DRY_RUN:
        print(f"   - Tracks Realigned:    {moved_live}")
    print(f"   - Action:              {'Reported Only' if DRY_RUN else 'FTP Files Moved'}")
    print(f"\n Quality Upgrade Scan:")
    print(f"   - Tracks Flagged:      {to_upgrade}")
    print(f"   - Action:              {'Reported Only' if DRY_RUN else 'Upgrade Queue Generated'}")
    print(f"\n Database Sync Scan:")
    print(f"   - Missing Tracks:      {synced_missing}")
    print(f"   - Action:              {'Reported Only (.txt)' if DRY_RUN else 'Injected to CSV & Reported'}")
    print("="*80)
    if DRY_RUN:
        print("\n TIP: Set DRY_RUN = False at the top of the script to execute these changes.")
    print("="*80 + "\n")

if __name__ == "__main__":
    run_audit()
