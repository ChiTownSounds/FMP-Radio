import os
import csv

CSV_PATH = r"configs/fmp_data_7718.csv"
LYRICS_DIR = r"configs/lyrics"

def migrate():
    print("================================================================================")
    print("  FMP ULTIMATE - LYRICS SEPARATION MIGRATION")
    print("================================================================================\n")

    if not os.path.exists(CSV_PATH):
        print(f"[-] Error: CSV database not found at {CSV_PATH}")
        return

    os.makedirs(LYRICS_DIR, exist_ok=True)
    print(f"[*] Target Lyrics Directory: {LYRICS_DIR}")

    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    print(f"[*] Reading {len(rows)} records from master database...")

    migrated_count = 0
    cleaned_rows = []

    for index, row in enumerate(rows):
        track_name = row.get("Track Name", "").strip()
        lyrics = row.get("Lyrics", "").strip()

        # Clean/sanitize filename
        safe_track_name = "".join(c for c in track_name if c not in r'\/:*?"<>|').strip()

        if lyrics and len(lyrics) > 15 and lyrics not in ["Unknown", "Not Found", "True", "False"]:
            # This is full lyrics text! Let's save it to a separate text file
            txt_filename = f"{safe_track_name}.txt"
            txt_filepath = os.path.join(LYRICS_DIR, txt_filename)
            
            try:
                with open(txt_filepath, 'w', encoding='utf-8') as lyrics_file:
                    lyrics_file.write(lyrics)
                
                # Update row to just contain "True" as the boolean flag
                row["Lyrics"] = "True"
                migrated_count += 1
            except Exception as e:
                print(f"[-] Warning: Failed to write lyrics file for '{track_name}': {e}")
        elif lyrics == "True" or (lyrics and len(lyrics) <= 15 and lyrics.lower() not in ["unknown", "not found", "false"]):
            row["Lyrics"] = "True"
        else:
            row["Lyrics"] = "Unknown"

        cleaned_rows.append(row)

    # Write the cleaned CSV back in place
    print(f"\n[*] Writing cleaned records to {CSV_PATH}...")
    try:
        with open(CSV_PATH, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(cleaned_rows)
        print(f"[+] SUCCESS: Migrated {migrated_count} tracks' lyrics to separate files.")
        print(f"[+] The CSV has been compressed and contains exactly {len(cleaned_rows) + 1} physical lines!")
    except Exception as e:
        print(f"[-] Error writing CSV back: {e}")

if __name__ == "__main__":
    migrate()
