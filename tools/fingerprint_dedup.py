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
from config import CSV_BLUEPRINT, MUSIC_DIR, is_non_song
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


def version_tag(track_name, filename=""):
    """Same tag vocabulary as app.py's is_smart_duplicate()/norm_title():
    a title carrying an explicit version marker is a deliberately distinct
    edit, not a stray duplicate of its sibling. Returns None for a bare
    title (the common case -- most titles carry no marker at all).

    Checks the filename too, not just Track Name -- this project's own
    convention (storage.py) deliberately never writes "(Explicit)" into
    Track Name (explicit is the unmarked default state), only into the
    filename. Confirmed live 2026-09-15: 'Diana Ross - Endless Love
    (Reprise)' matched itself with no version tag on either side by Track
    Name alone, even though one file's actual filename carries
    "(Explicit)" -- the filename is what reliably carries the marker."""
    t = f"{track_name} {filename}".lower()
    if 'radio edit' in t or 'radio version' in t:
        return 'radio_edit'
    if 'clean' in t:
        return 'clean'
    if 'explicit' in t:
        return 'explicit'
    return None


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
    seen_paths = set()
    skipped_dupe_rows = 0
    for idx, row in enumerate(rows):
        track_name = row.get('Track Name', '').strip()
        if not track_name:
            continue
        g_path = get_absolute_gpath(row.get('File Path', ''))
        if not os.path.exists(g_path):
            continue

        # The CSV has ~21 known file paths that appear as more than one row
        # (a separate data-integrity issue, not this tool's job to fix) --
        # without this guard, the same physical file gets added to the pool
        # once per duplicate row, and since two rows for the same file
        # naturally share one fingerprint, it "matches itself" at a trivial
        # 1.0000 score. That's a duplicate CSV row, not a duplicate audio
        # file, and was flooding the report with noise (confirmed against
        # the Sept 3 report: e.g. "Aaliyah - Come Back in One Piece" showed
        # 3 self-pairs, all pointing at the exact same path).
        norm_path = str(g_path).lower()
        if norm_path in seen_paths:
            skipped_dupe_rows += 1
            continue
        seen_paths.add(norm_path)

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
            "is_song": not is_non_song(track_name, row.get('File Path', '')),
        })

    if skipped_dupe_rows:
        print(f"[*] Skipped {skipped_dupe_rows} CSV rows pointing at a physical file already "
              f"seen under another row (duplicate CSV rows, not duplicate audio).")

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

    # A high fingerprint score between an Explicit/base track and its own
    # Clean or Radio Edit sibling is EXPECTED, not evidence of an accidental
    # duplicate -- a censored edit still shares ~99% of its audio with the
    # original, so it scores just as high as a real duplicate would. Score
    # alone can't tell those two cases apart; the version tag can. Confirmed
    # live 2026-09-15 against this exact report: ~80 of the matches below
    # are legitimate Explicit/Clean/Radio-Edit pairs that must stay as two
    # files, not a true-duplicate list to act on as-is.
    true_dupes, version_pairs, asset_matches = [], [], []
    for a, b, score in matches:
        if not (a["is_song"] and b["is_song"]):
            asset_matches.append((a, b, score))
            continue
        tag_a = version_tag(a["track_name"], os.path.basename(a["path"]))
        tag_b = version_tag(b["track_name"], os.path.basename(b["path"]))
        if tag_a != tag_b:
            version_pairs.append((a, b, score))
        else:
            true_dupes.append((a, b, score))

    print("\n" + "=" * 70)
    print(" SCAN COMPLETE")
    print("=" * 70)
    print(f" Pairs compared:              {compared_pairs}")
    print(f" Matches >= {args.threshold:.2f}:            {len(matches)}")
    print(f"   Likely true duplicates:    {len(true_dupes)}  (candidates to consolidate)")
    print(f"   Legitimate version pairs:  {len(version_pairs)}  (Explicit/Clean/Radio Edit -- keep both)")
    print(f"   Non-song asset matches:    {len(asset_matches)}  (sweepers/beds/IDs -- a branding call, not a data-accuracy one)")
    print("=" * 70)

    for a, b, score in true_dupes:
        print(f"  [{score:.4f}] '{a['track_name']}'  <->  '{b['track_name']}'")

    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(REPORT_PATH, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write(" FMP FINGERPRINT-BASED DUPLICATE REPORT\n")
            f.write("=" * 80 + "\n")
            f.write(f"Threshold: {args.threshold} | Window: {args.window_ms}ms | Compared: {compared_pairs} pairs\n")
            f.write("Report-only - nothing was deleted or modified.\n")
            f.write("-" * 80 + "\n\n")

            f.write(f"### LIKELY TRUE DUPLICATES ({len(true_dupes)}) ###\n")
            f.write("Same or no version tag on both sides -- not a legitimate Explicit/Clean/\n")
            f.write("Radio Edit pair. Review before consolidating, but these are the real\n")
            f.write("candidates.\n\n")
            for a, b, score in true_dupes:
                f.write(f"Score: {score:.4f}\n  A: {a['track_name']}\n     {a['path']}\n"
                        f"  B: {b['track_name']}\n     {b['path']}\n\n")

            f.write(f"\n### LEGITIMATE VERSION PAIRS ({len(version_pairs)}) - DO NOT DELETE EITHER SIDE ###\n")
            f.write("High fingerprint similarity here is expected -- an Explicit/Clean/Radio\n")
            f.write("Edit pair shares nearly all of its audio by definition. Listed for\n")
            f.write("visibility only.\n\n")
            for a, b, score in version_pairs:
                f.write(f"Score: {score:.4f}\n  A: {a['track_name']}\n     {a['path']}\n"
                        f"  B: {b['track_name']}\n     {b['path']}\n\n")

            f.write(f"\n### NON-SONG PRODUCTION ASSET MATCHES ({len(asset_matches)}) ###\n")
            f.write("Sweepers/beds/station IDs/drops. A match here may be intentional reuse\n")
            f.write("(the same generic opener used across different show names) or accidental\n")
            f.write("duplication -- a station-branding judgment call, not a data-accuracy one.\n\n")
            for a, b, score in asset_matches:
                f.write(f"Score: {score:.4f}\n  A: {a['track_name']}\n     {a['path']}\n"
                        f"  B: {b['track_name']}\n     {b['path']}\n\n")

        print(f"\n[✓] Report saved to: {REPORT_PATH}")
    except Exception as e:
        print(f"[!] Failed to write report file: {e}")


if __name__ == "__main__":
    main()
