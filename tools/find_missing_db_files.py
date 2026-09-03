import os
import sys
import csv
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CSV_BLUEPRINT, MUSIC_DIR

G_DRIVE_MUSIC = MUSIC_DIR

def get_absolute_gpath(file_path_on_server):
    clean_rel = file_path_on_server.replace('\\', '/')
    if clean_rel.upper().startswith('Z:/'):
        clean_rel = clean_rel[3:]
    elif clean_rel.lower().startswith('/home/ubuntu/music/'):
        clean_rel = clean_rel[len('/home/ubuntu/music/'):]
    return os.path.join(G_DRIVE_MUSIC, clean_rel.replace('/', os.sep))

def main():
    if not os.path.exists(CSV_BLUEPRINT):
        print("Database not found.")
        return

    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Loaded {len(rows)} rows from database.")

    missing_tracks = []
    for idx, row in enumerate(rows):
        track_name = row.get('Track Name', '').strip()
        file_path = row.get('File Path', '').strip()
        if not track_name:
            continue
            
        gpath = get_absolute_gpath(file_path)
        if not os.path.exists(gpath):
            missing_tracks.append((idx, row))

    print(f"Found {len(missing_tracks)} missing files in database:")
    for idx, row in missing_tracks:
        print(f"  [{idx}] '{row['Track Name']}' -> {row['File Path']}")

if __name__ == '__main__':
    main()
