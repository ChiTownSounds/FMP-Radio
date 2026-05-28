import os
import csv
import threading
from ftplib import FTP
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
    print("\n[PHASE 2] Scanning Remote FTP for Misplaced Live Tracks...")
    era_folders = ["Classics", "Old School 70s80s", "Throwbacks 90s2000s", "New School 2010+", "Unsorted_Review"]
    live_found_count = 0
    move_success_count = 0

    try:
        ftp = FTP()
        ftp.connect(FTP_HOST, FTP_PORT, timeout=15)
        ftp.login(FTP_USER, FTP_PASS)
        ftp.set_pasv(True)
        
        # Ensure Live directory exists
        try:
            ftp.cwd("/Live")
        except:
            if not DRY_RUN:
                print("  > Creating missing /Live directory...")
                ftp.mkd("/Live")
            else:
                print("  > [FLAGGED] /Live directory needs to be created.")

        for era in era_folders:
            remote_path = f"/{era}"
            try:
                ftp.cwd(remote_path)
                files = ftp.nlst()
                for filename in files:
                    if "live" in filename.lower() and filename.endswith(".mp3"):
                        live_found_count += 1
                        old_path = f"{remote_path}/{filename}"
                        new_path = f"/Live/{filename}"
                        
                        if DRY_RUN:
                            print(f"  > [FLAGGED LIVE] {old_path} -> {new_path}")
                        else:
                            try:
                                print(f"  > [MOVING] {filename}...")
                                ftp.rename(old_path, new_path)
                                move_success_count += 1
                            except Exception as e:
                                print(f"    !! Move Failed: {e}")
            except Exception as e:
                print(f"  !! Could not scan /{era}: {e}")
                continue

        ftp.quit()
    except Exception as e:
        print(f"!! FTP Connection Error: {e}")

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
    print("="*80)
    if DRY_RUN:
        print("\n TIP: Set DRY_RUN = False at the top of the script to execute these changes.")
    print("="*80 + "\n")

if __name__ == "__main__":
    run_audit()
