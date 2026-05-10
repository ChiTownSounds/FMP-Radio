import sys
import os
import glob

# Import the configured paths and our custom modules
from config import STAGING_DIR
from modules.ingest import Gatekeeper
from modules.download import Transporter
from modules.storage import VaultManager

def get_staged_file() -> str:
    """Finds the newly downloaded MP3 sitting in the staging directory."""
    # Look for any .mp3 file inside the staging folder
    search_pattern = os.path.join(STAGING_DIR, "*.mp3")
    staged_files = glob.glob(search_pattern)
    
    if staged_files:
        return staged_files[0] # Return the absolute path of the file
    return ""

def main():
    print("===========================================")
    print("   FMP ULTIMATE: INGESTION PIPELINE LIVE   ")
    print("===========================================")
    print("Initializing modules...")
    
    # Booting up the organs
    gatekeeper = Gatekeeper()
    transporter = Transporter()
    vault = VaultManager()
    
    print("System Online. Ready for URLs.\n")

    while True:
        try:
            url = input("Enter YouTube Music URL (or 'exit' to stop): ").strip()
            
            if url.lower() in ['exit', 'quit']:
                print("Initiating shutdown sequence...")
                break
            if not url:
                continue

            # ==========================================
            # PHASE 1: THE BOUNCER
            # ==========================================
            print("\n[PHASE 1] Gatekeeper: Validating metadata...")
            is_valid, result_data = gatekeeper.process_request(url)
            
            if not is_valid:
                error_msg = result_data.get('error', 'Unknown validation failure')
                print(f"[REJECTED] {error_msg}")
                continue
                
            metadata = result_data
            print("[APPROVED] Track meets FMP standards.")

            # ==========================================
            # PHASE 2: THE TRANSPORTER
            # ==========================================
            print("[PHASE 2] Transporter: Downloading via SomeDL...")
            # Remember: Our actual script returns True/False, not a path!
            download_success = transporter.download_track(url)
            
            if not download_success:
                print("[ERROR] Download failed. Moving to next URL.")
                continue

            # ==========================================
            # PHASE 3: THE VAULT
            # ==========================================
            file_path = get_staged_file()
            
            if not file_path:
                print(f"[ERROR] Transporter succeeded, but no MP3 was found in {STAGING_DIR}.")
                continue

            print(f"[PHASE 3] Vault: Tagging and moving to Z:\\ drive...")
            success = vault.store_track(file_path, metadata)
            
            if success:
                print("[PIPELINE COMPLETE] Track secured and CSV updated.")
            else:
                print("[ERROR] Vault storage failed. File may be stuck in staging.")

        except KeyboardInterrupt:
            print("\nProcess interrupted by user. Shutting down...")
            break
        except Exception as e:
            print(f"\n[CRITICAL ERROR] Pipeline crashed: {e}")

if __name__ == "__main__":
    main()