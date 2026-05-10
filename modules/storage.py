import os
import shutil
import csv
import logging
import re
import glob
from typing import Dict, Any, Tuple, List
from thefuzz import process

from config import STAGING_DIR, SERVER_DIR, CSV_BLUEPRINT

class VaultManager:
    def __init__(self):
        self.era_folders = [
            "Classics", 
            "Old School 70s80s", 
            "Throwbacks 90s2000s", 
            "New School 2010+", 
            "Live", 
            "Unsorted_Review"
        ]
        if not os.path.exists(SERVER_DIR):
            logging.error(f"Failed to access SERVER_DIR on Z: drive at {SERVER_DIR}")

    def _sanitize_filename(self, name: str) -> str:
        return re.sub(r'[<>:"/\\|?*]', '', name).strip()

    def _determine_folder(self, year_str: str, track_title: str) -> str:
        if "(live)" in track_title.lower():
            return "Live"
        try:
            year = int(str(year_str)[:4])
            if year <= 1969: return "Classics"
            elif 1970 <= year <= 1989: return "Old School 70s80s"
            elif 1990 <= year <= 2009: return "Throwbacks 90s2000s"
            elif year >= 2010: return "New School 2010+"
        except (ValueError, TypeError):
            return "Unsorted_Review"

    def find_candidates(self, search_query: str) -> List[Dict]:
        all_tracks = []
        try:
            if not os.path.exists(CSV_BLUEPRINT): return []
            with open(CSV_BLUEPRINT, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                all_tracks = list(reader)
            names = [row['Track Name'] for row in all_tracks if 'Track Name' in row]
            matches = process.extract(search_query, names, limit=20)
            results = []
            for name, score in matches:
                if score > 35:
                    for row in all_tracks:
                        if row['Track Name'] == name:
                            results.append(row); break
            return results
        except Exception: return []

    def scrub_track(self, exact_title: str) -> Tuple[bool, str]:
        rows_to_keep = []
        track_found_in_ledger = False
        file_deleted = False
        location_msg = "File not found on drive"

        try:
            with open(CSV_BLUEPRINT, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
                for row in reader:
                    if row['Track Name'].strip() == exact_title.strip():
                        track_found_in_ledger = True
                        
                        for folder in self.era_folders:
                            target_dir = os.path.join(SERVER_DIR, folder)
                            if not os.path.exists(target_dir): continue
                            
                            file_path = os.path.join(target_dir, f"{exact_title}.mp3")
                            
                            if not os.path.exists(file_path):
                                files = glob.glob(os.path.join(target_dir, "*.mp3"))
                                if files:
                                    names_only = [os.path.basename(f) for f in files]
                                    match, score = process.extractOne(f"{exact_title}.mp3", names_only)
                                    if score > 90:
                                        file_path = os.path.join(target_dir, match)

                            if os.path.exists(file_path):
                                os.remove(file_path)
                                file_deleted = True
                                location_msg = f"Successfully deleted from {folder}"
                                break
                    else:
                        rows_to_keep.append(row)

            if track_found_in_ledger:
                with open(CSV_BLUEPRINT, mode='w', encoding='utf-8', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows_to_keep)
                return True, location_msg
            
            return False, "Track not found in ledger."
        except Exception as e:
            return False, str(e)

    def store_track(self, file_path: str, metadata: Dict[str, Any]) -> bool:
        if not os.path.exists(file_path): return False
        artist = self._sanitize_filename(metadata.get('artist', 'Unknown Artist'))
        title = self._sanitize_filename(metadata.get('title', 'Unknown Title'))
        clean_name = f"{artist} - {title}"
        year = str(metadata.get('release_year') or metadata.get('upload_date', 'Unknown'))
        category = self._determine_folder(year, clean_name)
        final_dir = os.path.join(SERVER_DIR, category)
        if not os.path.exists(final_dir): return False
        dest_path = os.path.join(final_dir, f"{clean_name}.mp3")
        try:
            shutil.copy2(file_path, dest_path)
            os.remove(file_path)
            self._update_csv(metadata, clean_name)
            return True
        except: return False

    # THE FULL CSV LOGIC RESTORED
    def _update_csv(self, metadata: Dict[str, Any], clean_title: str):
        rows = []
        updated = False
        
        new_bitrate = metadata.get('abr', 'Unknown')
        new_year = metadata.get('release_year') or metadata.get('upload_date', 'Unknown')
        
        new_ratio = 'Unknown'
        thumbnails = metadata.get('thumbnails', [])
        if thumbnails:
            best_thumb = thumbnails[-1]
            w, h = best_thumb.get('width', 0), best_thumb.get('height', 0)
            if w and h: new_ratio = round(w / h, 2)

        try:
            with open(CSV_BLUEPRINT, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames or ['Track Name', 'Bitrate', 'Lyrics', 'Year', 'Art Ratio']
                for row in reader:
                    if row.get('Track Name', '').strip().lower() == clean_title.lower():
                        row['Bitrate'] = new_bitrate
                        row['Year'] = new_year
                        row['Art Ratio'] = new_ratio
                        updated = True
                    rows.append(row)
        except FileNotFoundError:
            fieldnames = ['Track Name', 'Bitrate', 'Lyrics', 'Year', 'Art Ratio']

        if not updated:
            rows.append({
                'Track Name': clean_title,
                'Bitrate': new_bitrate,
                'Lyrics': 'False', 
                'Year': new_year,
                'Art Ratio': new_ratio
            })

        with open(CSV_BLUEPRINT, mode='w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)