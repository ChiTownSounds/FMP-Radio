import os
import sys
import re
import csv
import io
import argparse
from pathlib import Path
from collections import defaultdict

# Fix encoding for Windows consoles
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT_DIR)
from config import CSV_BLUEPRINT, MUSIC_DIR
from modules.fingerprint_compare import compare_fingerprints

from mutagen.id3 import ID3

G_DRIVE_MUSIC = MUSIC_DIR
REPORT_PATH = Path(os.path.join(ROOT_DIR, "logs", "fingerprint_dupes.txt"))

DEFAULT_THRESHOLD = 0.90
DEFAULT_WINDOW_MS = 2500  # candidates must be within this many ms of duration to be compared


def get_absolute_gpath(file_path_on_server):
    clean_rel = file_path_on_server.replace('\\', '/')
    if clean_rel.upper().startswith('Z:/'):
        clean_rel = clean_rel[3:]
    elif clean_rel.lower().startswith('/home/ubuntu/music/'):
        clean_rel = clean_rel[len('/home/ubuntu/music/'):]
    return os.path.join(G_DRIVE_MUSIC, clean_rel.replace('/', os.sep))


def clean_artist_key(track_name):
    artist = track_name.split(' - ')[0] if ' - ' in track_name else ''
    artist_clean = re.split(r'\s+(feat\.?|featuring|with|w/|f/|and|&)\s+', artist.lower())[0]
    return re.sub(r'[^a-z0-9]', '', artist_clean)


def get_fingerprint(row, g_path):
    """CSV column first (cheap); falls back to reading the file's own
    TXXX:AUDIO_FINGERPRINT tag if the CSV cell hasn't been backfilled yet."""
    fp = row.get('Fingerprint', '').strip()
    if fp:
        return fp
    try:
        id3 = ID3(g_path)
        key = 'TXXX:AUDIO_FINGERPRINT'
        if key in id3 and id3[key].text:
            return str(id3[key].text[0]).strip() or None
    except Exception:
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description="FMP Fingerprint-Based Duplicate Detector (report-only)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                         help=f"Minimum similarity score [0,1] to report as a match (default {DEFAULT_THRESHOLD})")
    parser.add_argument("--window-ms", type=int, default=DEFAULT_WINDOW_MS,
                         help=f"Max duration difference (ms) between candidates to compare (default {DEFAULT_WINDOW_MS})")
    parser.add_argument("--limit", type=int, default=None, help="Only scan the first N tracks with a fingerprint")
    args = parser.parse_args()

    print("=" * 70)
    print(" FMP FINGERPRINT-BASED DUPLICATE DETECTOR (REPORT-ONLY)")
    print("=" * 70)

    if not os.path.exists(CSV_BLUEPRINT):
        print(f"[FATAL] Database CSV not found at {CSV_BLUEPRINT}")
        return

    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"[*] Loaded {len(rows)} tracks from database.")

    candidates = []
    missing_fp = 0
    for idx, row in enumerate(rows):
        track_name = row.get('Track Name', '').strip()
        if not track_name:
            continue
        g_path = get_absolute_gpath(row.get('File Path', ''))
        if not os.path.exists(g_path):
            continue
        try:
            duration_ms = int(row.get('duration_ms', 0) or 0)
        except ValueError:
            duration_ms = 0
        if duration_ms <= 0:
            continue

        fingerprint = get_fingerprint(row, g_path)
        if not fingerprint:
            missing_fp += 1
            continue

        candidates.append({
            "idx": idx,
            "track_name": track_name,
            "artist_key": clean_artist_key(track_name),
            "duration_ms": duration_ms,
            "fingerprint": fingerprint,
            "path": str(g_path),
        })

    if args.limit:
        candidates = candidates[:args.limit]

    print(f"[*] {len(candidates)} tracks have a usable fingerprint "
          f"({missing_fp} on disk but missing one - run backfill_fingerprints.py first).")

    artist_groups = defaultdict(list)
    for c in candidates:
        artist_groups[c["artist_key"]].append(c)

    print(f"[*] Grouped into {len(artist_groups)} artist buckets. Comparing within "
          f"{args.window_ms}ms duration windows...")

    matches = []
    compared_pairs = 0
    for artist_key, items in artist_groups.items():
        if len(items) < 2:
            continue
        items.sort(key=lambda c: c["duration_ms"])
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if items[j]["duration_ms"] - items[i]["duration_ms"] > args.window_ms:
                    break
                compared_pairs += 1
                score = compare_fingerprints(items[i]["fingerprint"], items[j]["fingerprint"])
                if score >= args.threshold:
                    matches.append((items[i], items[j], score))

    matches.sort(key=lambda m: m[2], reverse=True)

    print("\n" + "=" * 70)
    print(" SCAN COMPLETE")
    print("=" * 70)
    print(f" Pairs compared:        {compared_pairs}")
    print(f" Matches >= {args.threshold:.2f}:      {len(matches)}")
    print("=" * 70)

    for a, b, score in matches:
        print(f"  [{score:.4f}] '{a['track_name']}'  <->  '{b['track_name']}'")

    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(REPORT_PATH, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write(" FMP FINGERPRINT-BASED DUPLICATE REPORT\n")
            f.write("=" * 80 + "\n")
            f.write(f"Threshold: {args.threshold} | Window: {args.window_ms}ms | "
                     f"Compared: {compared_pairs} pairs | Matches: {len(matches)}\n")
            f.write("Report-only - nothing was deleted or modified. Review matches manually\n")
            f.write("before wiring any of this into deduplicate_library.py's deletion logic.\n")
            f.write("-" * 80 + "\n\n")
            for a, b, score in matches:
                f.write(f"Score: {score:.4f}\n")
                f.write(f"  A: {a['track_name']}\n")
                f.write(f"     {a['path']}\n")
                f.write(f"  B: {b['track_name']}\n")
                f.write(f"     {b['path']}\n\n")
        print(f"\n[✓] Report saved to: {REPORT_PATH}")
    except Exception as e:
        print(f"[!] Failed to write report file: {e}")


if __name__ == "__main__":
    main()
