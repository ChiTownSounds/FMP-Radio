import sys
import os
import glob
import subprocess

# Import the configured paths and our custom modules
from config import STAGING_DIR, YT_DLP_CMD
from modules.ingest import Gatekeeper
from modules.download import Transporter
from modules.storage import VaultManager

def extract_playlist_urls(playlist_url: str) -> list:
    """Uses yt-dlp to quickly flatten a playlist and strictly filters for single tracks."""
    print(f"[EXTRACTOR] Analyzing playlist... Stand by.")
    
    cmd = YT_DLP_CMD + [
        "--flat-playlist", 
        "--print", "url", 
        playlist_url
    ]
    
    try:
        # Run the command and capture the text output
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # Split by line breaks, and strictly filter for ACTUAL single tracks
        urls = []
        for line in result.stdout.split('\n'):
            clean_line = line.strip()
            # Only keep URLs that are definitely individual videos/songs
            if 'watch?v=' in clean_line or 'youtu.be/' in clean_line:
                urls.append(clean_line)
                
        return urls
        
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to extract playlist. yt-dlp error: {e.stderr}")
        return []
    except Exception as e:
        print(f"[ERROR] System failure during playlist extraction: {e}")
        return []

def main():
    print("===========================================")
    print("   FMP ULTIMATE: INGESTION PIPELINE LIVE   ")
    print("===========================================")
    print("Initializing modules...")
    
    gatekeeper = Gatekeeper()
    transporter = Transporter()
    vault = VaultManager()
    
    print("System Online. Ready for URLs or Playlists.\n")

    while True:
        try:
            # 1. Ask for the URL
            url_input = input("Enter YouTube Music URL or Playlist (or 'exit' to stop): ").strip()
            
            if url_input.lower() in ['exit', 'quit']:
                print("Initiating shutdown sequence...")
                break
            if not url_input:
                continue

            # ==========================================
            # THE TRAFFIC COP
            # ==========================================
            # Check if it's a playlist URL
            if "list=" in url_input or "playlist" in url_input.lower():
                urls_to_process = extract_playlist_urls(url_input)
                if not urls_to_process:
                    print("[ERROR] No tracks found or playlist is private.")
                    continue
                print(f"[EXTRACTOR] Successfully flattened! Found {len(urls_to_process)} tracks ready for ingestion.")
            else:
                # It's just a single song, put it in a list so we can loop it anyway
                urls_to_process = [url_input]

            # ==========================================
            # THE PROCESSING LOOP
            # ==========================================
            # Loop through however many URLs we have (1 or 100)
            for index, url in enumerate(urls_to_process, 1):
                if len(urls_to_process) > 1:
                    print(f"\n--- [TRACK {index}/{len(urls_to_process)}] ---")
                else:
                    print("\n--- [SINGLE TRACK] ---")

                # --- PHASE 1: THE BOUNCER ---
                print(f"[PHASE 1] Gatekeeper: Validating {url}")
                is_valid, result_data = gatekeeper.process_request(url)
                
                if not is_valid:
                    error_msg = result_data.get('error', 'Unknown validation failure')
                    print(f"[REJECTED] {error_msg}. Moving to next...")
                    continue 
                    
                metadata = result_data
                track_name = metadata.get('title', 'Unknown Title')
                print(f"[APPROVED] '{track_name}' meets FMP standards.")

                # --- PHASE 2: THE TRANSPORTER ---
                print(f"[PHASE 2] Transporter: Downloading via SomeDL...")
                dl_result = transporter.download_track(url)
                
                # dl_result is a tuple: (file_path, bitrate)
                file_path = dl_result[0]
                
                if not file_path:
                    print("[ERROR] Download failed or no file found. Moving to next URL.")
                    continue

                # --- PHASE 3: THE VAULT ---
                print(f"[PHASE 3] Vault: Tagging and moving {os.path.basename(file_path)} to Z:\\ drive...")
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