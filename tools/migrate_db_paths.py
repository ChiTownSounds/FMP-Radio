import os
import shutil
import csv
import sqlite3

def run_migration():
    # Paths
    csv_path = r"C:\FMP_Ultimate\configs\fmp_data_7718.csv"
    csv_backup = r"C:\FMP_Ultimate\configs\fmp_data_7718.csv.pre_untangle"
    db_path = r"C:\FMP_Broadcaster\fmp_radio.db"
    db_backup = r"C:\FMP_Broadcaster\fmp_radio.db.pre_untangle"

    print("=== Filepath Untangling Migration Script ===")

    # 1. Backup CSV
    if os.path.exists(csv_path):
        if not os.path.exists(csv_backup):
            print(f"[Backup] Copying {csv_path} to {csv_backup}...")
            shutil.copy2(csv_path, csv_backup)
        else:
            print(f"[Backup] {csv_backup} already exists, skipping backup.")
    else:
        print(f"[Error] CSV file not found at {csv_path}!")
        return

    # 2. Backup DB
    if os.path.exists(db_path):
        if not os.path.exists(db_backup):
            print(f"[Backup] Copying {db_path} to {db_backup}...")
            shutil.copy2(db_path, db_backup)
        else:
            print(f"[Backup] {db_backup} already exists, skipping backup.")
    else:
        print(f"[Warning] SQLite DB not found at {db_path}, skipping DB backup/migration.")
        db_path = None

    # 3. Migrate CSV
    print(f"[Migration] Reading CSV {csv_path}...")
    rows = []
    fieldnames = []
    modified_csv_count = 0
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            fp = row.get("File Path", "")
            if fp:
                # Normalize slash and strip Z:/ or Z:\
                clean_fp = fp.replace('\\', '/')
                if clean_fp.upper().startswith('Z:/'):
                    row["File Path"] = clean_fp[3:]
                    modified_csv_count += 1
            rows.append(row)

    if modified_csv_count > 0:
        print(f"[Migration] Rewriting CSV with {modified_csv_count} paths updated...")
        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print("[Migration] CSV successfully migrated.")
    else:
        print("[Migration] No paths in CSV needed modification.")

    # 4. Migrate DB
    if db_path:
        print(f"[Migration] Connecting to DB {db_path}...")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # We need to strip Z:/ and Z:\ from media_library and playlist_items
        # In SQLite, we can use SUBSTR(file_path, 4) if it starts with Z:/ or Z:\ (case-insensitive)
        # Note: LIKE is case-insensitive in SQLite by default for ASCII characters.
        cursor.execute("SELECT COUNT(*) FROM media_library WHERE file_path LIKE 'Z:/%' OR file_path LIKE 'Z:\%'")
        ml_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM playlist_items WHERE file_path LIKE 'Z:/%' OR file_path LIKE 'Z:\%'")
        pi_count = cursor.fetchone()[0]
        
        print(f"[Migration] Found {ml_count} records in media_library and {pi_count} records in playlist_items to migrate.")
        
        if ml_count > 0:
            cursor.execute("""
                UPDATE media_library 
                SET file_path = SUBSTR(file_path, 4) 
                WHERE file_path LIKE 'Z:/%' OR file_path LIKE 'Z:\%'
            """)
            print(f"[Migration] Updated media_library.")
            
        if pi_count > 0:
            cursor.execute("""
                UPDATE playlist_items 
                SET file_path = SUBSTR(file_path, 4) 
                WHERE file_path LIKE 'Z:/%' OR file_path LIKE 'Z:\%'
            """)
            print(f"[Migration] Updated playlist_items.")
            
        conn.commit()
        conn.close()
        print("[Migration] SQLite DB successfully migrated.")
        
    print("=== Migration Completed Successfully ===")

if __name__ == "__main__":
    run_migration()
