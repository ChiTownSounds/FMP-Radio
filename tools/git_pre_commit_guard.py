import csv
import sys
import os
import subprocess

# Reconfigure stdout/stderr error handlers to prevent Windows encoding crashes when printing Unicode
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(errors='replace')

VALID_DIRS = [
    'Classics', 'Old School 70s80s', 'Throwbacks 90s2000s', 'New School 2010+',
    'Live', 'Unsorted_Review', '365 Commercials', '90s2000s', '80s', 'Today',
    'STAGING', 'intro', 'ondemand', 'Shows'
]

def check_and_clean_csv():
    # Get the repository root directory
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(root_dir, "configs", "fmp_data_7718.csv")
    
    if not os.path.exists(csv_path):
        print(f"[PRE-COMMIT GUARD] Warning: Database CSV not found at {csv_path}. Skipping check.")
        sys.exit(0)

    print("[PRE-COMMIT GUARD] Auto-sorting and cleaning database CSV...")
    
    rows = []
    fieldnames = []
    modified = False
    invalid_rows = []

    # 1. Read and parse/correct rows
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            
            for idx, row in enumerate(reader, start=2):  # Line 1 is header
                track_name = row.get("Track Name", "").strip()
                file_path = row.get("File Path", "").strip()
                
                if not file_path:
                    rows.append(row)
                    continue
                
                # Clean path
                cleaned_path = file_path.replace('\\', '/')
                # Normalize double slashes
                while '//' in cleaned_path:
                    cleaned_path = cleaned_path.replace('//', '/')
                
                # Check if it starts with a drive letter (e.g. Z:/, z:/, G:/, C:/) and strip it
                import re
                drive_match = re.match(r'^[A-Za-z]:/(.*)', cleaned_path)
                if drive_match:
                    cleaned_path = drive_match.group(1)
                    modified = True
                
                # Check if the path starts with one of the VALID_DIRS
                prefix_match = False
                for d in VALID_DIRS:
                    if cleaned_path.lower().startswith(d.lower() + '/'):
                        prefix_match = True
                        break
                
                if not prefix_match:
                    invalid_rows.append((idx, track_name, file_path, "Path does not start with a valid directory"))
                else:
                    if cleaned_path != file_path:
                        row['File Path'] = cleaned_path
                        modified = True
                
                rows.append(row)
                
    except Exception as e:
        print(f"[PRE-COMMIT GUARD] [ERROR] Failed to read database: {e}")
        sys.exit(1)

    # 2. Block commit if there are completely unresolvable errors
    if invalid_rows:
        print("\n" + "!" * 80)
        print(" [COMMIT BLOCKED] Database Path Consistency Violation Detected!")
        print("!" * 80)
        print("The following tracks have file paths that cannot be auto-corrected:")
        print("-" * 80)
        for line_num, track, path, reason in invalid_rows[:10]:
            print(f"  Line {line_num}: '{track}' -> Path: {path} ({reason})")
        if len(invalid_rows) > 10:
            print(f"  ... and {len(invalid_rows) - 10} more rows.")
        print("-" * 80)
        print("Please fix the file paths in configs/fmp_data_7718.csv before committing.")
        print("!" * 80 + "\n")
        sys.exit(1)

    # 3. Sort rows alphabetically by Track Name (case-insensitive)
    original_order = [r.get("Track Name", "") for r in rows]
    rows.sort(key=lambda r: r.get("Track Name", "").lower())
    new_order = [r.get("Track Name", "") for r in rows]
    
    if original_order != new_order:
        print("[PRE-COMMIT GUARD] Database rows sorted alphabetically by Track Name.")
        modified = True

    # 4. Write CSV back to disk if modified
    if modified:
        try:
            with open(csv_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            print("[PRE-COMMIT GUARD] Database CSV successfully auto-corrected and rewritten.")
            
            # 5. Automatically stage the modified CSV file
            print("[PRE-COMMIT GUARD] Staging corrected configs/fmp_data_7718.csv...")
            subprocess.run(["git", "add", csv_path], check=True)
            print("[PRE-COMMIT GUARD] [OK] Staged successfully.")
        except Exception as e:
            print(f"[PRE-COMMIT GUARD] [ERROR] Failed to write and stage CSV: {e}")
            sys.exit(1)
    else:
        print("[PRE-COMMIT GUARD] [OK] Database is already sorted and formatted properly.")

    # 5. Run Frictionless Data Schema validation
    print("[PRE-COMMIT GUARD] Running Frictionless Data Schema validation...")
    try:
        cmd = [sys.executable, "-m", "frictionless", "validate", "configs/fmp_data_7718.csv", "--schema", "configs/schema.json"]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', cwd=root_dir)
        if res.returncode != 0:
            print("\n" + "!" * 80)
            print(" [COMMIT BLOCKED] Frictionless Schema Validation Failed!")
            print("!" * 80)
            print(res.stdout or res.stderr)
            print("!" * 80 + "\n")
            sys.exit(1)
        else:
            print("[PRE-COMMIT GUARD] [OK] Frictionless Schema Validation Passed.")
    except Exception as e:
        print(f"[PRE-COMMIT GUARD] Warning: Frictionless validation skipped: {e}")

    sys.exit(0)

if __name__ == "__main__":
    check_and_clean_csv()
