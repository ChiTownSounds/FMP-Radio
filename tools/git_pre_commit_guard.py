import csv
import sys
import os

def check_csv_paths():
    # Get the repository root directory
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(root_dir, "configs", "fmp_data_7718.csv")
    
    if not os.path.exists(csv_path):
        print(f"[PRE-COMMIT GUARD] Warning: Database CSV not found at {csv_path}. Skipping check.")
        sys.exit(0)

    print("[PRE-COMMIT GUARD] Scanning database CSV for path consistency...")
    invalid_rows = []
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            # Find the index/line number
            for idx, row in enumerate(reader, start=2):  # Line 1 is header
                track_name = row.get("Track Name", "")
                file_path = row.get("File Path", "")
                
                if not file_path:
                    continue
                
                # Check prefix
                clean_path = file_path.replace('\\', '/')
                if not clean_path.upper().startswith("Z:/"):
                    invalid_rows.append((idx, track_name, file_path))
                    
    except Exception as e:
        print(f"[PRE-COMMIT GUARD] [ERROR] Failed to read database: {e}")
        sys.exit(1)

    if invalid_rows:
        print("\n" + "!" * 80)
        print(" [COMMIT BLOCKED] Database Path Consistency Violation Detected!")
        print("!" * 80)
        print("The following tracks have file paths that do not start with the universal 'Z:/' prefix.")
        print("All database paths must start with 'Z:/' to maintain cross-platform compatibility.")
        print("-" * 80)
        for line_num, track, path in invalid_rows[:10]:
            print(f"  Line {line_num}: '{track}' -> Path: {path}")
        if len(invalid_rows) > 10:
            print(f"  ... and {len(invalid_rows) - 10} more rows.")
        print("-" * 80)
        print("Please fix the file paths in configs/fmp_data_7718.csv before committing.")
        print("If you absolutely need to commit this, use: git commit --no-verify")
        print("!" * 80 + "\n")
        sys.exit(1)

    print("[PRE-COMMIT GUARD] [OK] Database path check passed. All paths conform to 'Z:/' format.")
    sys.exit(0)

if __name__ == "__main__":
    check_csv_paths()
