import os
import sys

# Ensure the root dir is in sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from modules.ingest import Gatekeeper
from modules.download import Transporter
from modules.storage import VaultManager

def run_ingest(queue_file: str):
    if not os.path.exists(queue_file):
        print(f"[-] Error: Queue file '{queue_file}' does not exist.")
        return

    with open(queue_file, 'r', encoding='utf-8') as f:
        urls = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]

    if not urls:
        print(f"[*] Queue file '{queue_file}' is empty. Nothing to ingest.")
        return

    print("===========================================")
    print(f"   FMP ULTIMATE: DELTA INGESTION STARTED   ")
    print("===========================================")
    print(f"[*] Queue File: {queue_file}")
    print(f"[*] Total Tracks to Process: {len(urls)}")
    print("===========================================")

    gatekeeper = Gatekeeper()
    transporter = Transporter()
    vault = VaultManager()

    success_count = 0
    fail_count = 0

    for index, url in enumerate(urls, 1):
        print(f"\n--- [TRACK {index}/{len(urls)}] ---")
        
        # 1. Initialize loop variables explicitly to ensure a cold-start event
        metadata = None
        file_path = None
        result_data = None
        dl_result = None
        is_valid = False
        
        try:
            print(f"[PHASE 1] Gatekeeper: Validating {url}")
            is_valid, result_data = gatekeeper.process_request(url)
            
            if not is_valid:
                error_msg = result_data.get('error', 'Unknown validation failure') if result_data else 'Unknown validation failure'
                print(f"[REJECTED] {error_msg}. Moving to next...")
                fail_count += 1
                continue 
                
            metadata = result_data
            track_name = metadata.get('title', 'Unknown Title')
            print(f"[APPROVED] '{track_name}' meets FMP standards.")

            # --- PHASE 2: THE TRANSPORTER ---
            print(f"[PHASE 2] Transporter: Downloading via SomeDL...")
            dl_result = transporter.download_track(url)
            
            if dl_result and isinstance(dl_result, tuple) and len(dl_result) > 0:
                file_path = dl_result[0]
            else:
                file_path = None
            
            if not file_path:
                print("[ERROR] Download failed or no file found. Moving to next URL.")
                fail_count += 1
                continue

            # --- PHASE 3: THE VAULT ---
            # Extract target filename for the explicitly printed confirmation match
            clean_artist = vault._safe_filename(metadata.get('artist', 'Unknown Artist'))
            clean_title = vault._safe_filename(metadata.get('title', 'Unknown Title'))
            expected_filename = f"{clean_artist} - {clean_title}.mp3"
            
            print(f"[PHASE 3] Vault: Tagging and moving {os.path.basename(file_path)} to Z:\\ drive...")
            print(f"[*] Confirming Target Filename: {expected_filename}")
            
            success = vault.store_track(file_path, metadata)
            
            if success:
                print("[PIPELINE COMPLETE] Track secured and CSV updated.")
                success_count += 1
            else:
                print("[ERROR] Vault storage failed. File may be stuck in staging.")
                fail_count += 1
        finally:
            # Force absolute loop isolation and garbage collection of references
            metadata = None
            file_path = None
            result_data = None
            dl_result = None
            try:
                del metadata
            except NameError:
                pass
            try:
                del file_path
            except NameError:
                pass
            try:
                del result_data
            except NameError:
                pass
            try:
                del dl_result
            except NameError:
                pass

    print("\n" + "="*50)
    print(f"INGESTION COMPLETE. Successful: {success_count} | Failed: {fail_count}")
    print("="*50 + "\n")

if __name__ == "__main__":
    queue_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join("configs", "playlist_queue.txt")
    run_ingest(queue_path)
