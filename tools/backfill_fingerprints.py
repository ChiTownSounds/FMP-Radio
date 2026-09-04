import os
import sys
import csv
import io
import time
import shutil
import argparse
import subprocess
import concurrent.futures

# Fix encoding for Windows consoles
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CSV_BLUEPRINT, MUSIC_DIR, AUTO_GIT_PUSH, git_operation_lock, git_safe_pull

from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TXXX

TAG_DESC = 'AUDIO_FINGERPRINT'
G_DRIVE_MUSIC = MUSIC_DIR
CHECKPOINT_EVERY = 25


def get_fpcalc_path():
    import platform
    if platform.system() == "Windows":
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fpcalc.exe")
    return shutil.which("fpcalc") or "fpcalc"


FPCALC_PATH = get_fpcalc_path()


def get_absolute_gpath(file_path_on_server):
    clean_rel = file_path_on_server.replace('\\', '/')
    if clean_rel.upper().startswith('Z:/'):
        clean_rel = clean_rel[3:]
    elif clean_rel.lower().startswith('/home/ubuntu/music/'):
        clean_rel = clean_rel[len('/home/ubuntu/music/'):]
    return os.path.join(G_DRIVE_MUSIC, clean_rel.replace('/', os.sep))


def read_existing_tag(g_path):
    try:
        id3 = ID3(g_path)
        key = f'TXXX:{TAG_DESC}'
        if key in id3 and id3[key].text:
            val = str(id3[key].text[0]).strip()
            if val:
                return val
    except Exception:
        pass
    return None


def compute_fingerprint(g_path):
    import json
    try:
        cmd = [FPCALC_PATH, "-json", str(g_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                                 errors='replace', check=True, timeout=30)
        data = json.loads(result.stdout)
        return data.get("fingerprint") or None
    except Exception as e:
        print(f"  [!] fpcalc failed on {os.path.basename(g_path)}: {e}")
        return None


def write_tag(g_path, fingerprint):
    audio = MP3(g_path)
    if audio.tags is None:
        audio.add_tags()
    audio.tags.add(TXXX(encoding=3, desc=TAG_DESC, text=[fingerprint]))
    audio.save()


def process_row(idx, g_path, dry_run):
    """Runs in a worker thread. Returns (idx, fingerprint_or_None, status)."""
    existing = read_existing_tag(g_path)
    if existing:
        return idx, existing, "already-tagged"

    if dry_run:
        return idx, None, "would-fingerprint"

    fingerprint = compute_fingerprint(g_path)
    if not fingerprint:
        return idx, None, "failed"

    try:
        write_tag(g_path, fingerprint)
    except Exception as e:
        print(f"  [!] Failed to write tag to {os.path.basename(g_path)}: {e}")
        return idx, None, "failed"

    return idx, fingerprint, "fingerprinted"


def write_csv(csv_path, fieldnames, rows):
    """Atomic write with a short retry: a periodic checkpoint hitting a
    transient Windows file lock (AV scan, sync client, etc.) on the final
    rename shouldn't kill an otherwise-healthy multi-hour batch job - a
    prior run crashed outright on this exact failure, losing nothing on
    disk (fingerprints live in each file's own tag) but dying mid-run
    instead of just retrying at the next checkpoint."""
    tmp_path = csv_path + ".tmp"
    with open(tmp_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    last_error = None
    for attempt in range(5):
        try:
            os.replace(tmp_path, csv_path)
            return
        except OSError as e:
            last_error = e
            time.sleep(0.5 * (attempt + 1))
    raise last_error


def main():
    parser = argparse.ArgumentParser(description="FMP Audio Fingerprint Backfill (Chromaprint via fpcalc)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of tracks processed")
    parser.add_argument("--workers", type=int, default=4, help="Parallel fpcalc workers (default 4)")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report only, write nothing")
    args = parser.parse_args()

    print("=" * 70)
    print(f" FMP AUDIO FINGERPRINT BACKFILL (DRY_RUN: {args.dry_run})")
    print("=" * 70)

    if not os.path.exists(CSV_BLUEPRINT):
        print(f"[FATAL] Database CSV not found at {CSV_BLUEPRINT}")
        return

    with open(CSV_BLUEPRINT, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    if 'Fingerprint' not in fieldnames:
        print("[FATAL] CSV has no 'Fingerprint' column - expected it to already exist.")
        return

    print(f"[*] Loaded {len(rows)} tracks from database.")

    # Resolve which rows have a real file on disk to work on.
    targets = []
    for idx, row in enumerate(rows):
        track_name = row.get('Track Name', '').strip()
        if not track_name:
            continue
        g_path = get_absolute_gpath(row.get('File Path', ''))
        if os.path.exists(g_path):
            targets.append((idx, g_path))

    total_found = len(targets)
    if args.limit:
        targets = targets[:args.limit]
        print(f"[*] Limiting to first {len(targets)} of {total_found} tracks with a file on disk.")
    else:
        print(f"[*] {total_found} tracks have a file on disk to process.")

    already_tagged = 0
    fingerprinted = 0
    failed = 0
    processed_since_checkpoint = 0
    csv_dirty = False

    def checkpoint():
        nonlocal csv_dirty
        if args.dry_run or not csv_dirty:
            return
        try:
            write_csv(CSV_BLUEPRINT, fieldnames, rows)
            csv_dirty = False
            print(f"  [checkpoint] CSV synced ({fingerprinted} fingerprinted so far)")
        except OSError as e:
            # Leave csv_dirty=True so the next checkpoint retries - the
            # tags already on disk are the source of truth regardless.
            print(f"  [!] Checkpoint CSV write failed, will retry next checkpoint: {e}")

    start_time = time.time()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_idx = {
                executor.submit(process_row, idx, g_path, args.dry_run): idx
                for idx, g_path in targets
            }
            completed = 0
            for future in concurrent.futures.as_completed(future_to_idx):
                idx, fingerprint, status = future.result()
                completed += 1
                track_name = rows[idx].get('Track Name', '')

                if status == "already-tagged":
                    already_tagged += 1
                    if rows[idx].get('Fingerprint', '').strip() != fingerprint:
                        rows[idx]['Fingerprint'] = fingerprint
                        csv_dirty = True
                elif status == "fingerprinted":
                    fingerprinted += 1
                    rows[idx]['Fingerprint'] = fingerprint
                    csv_dirty = True
                    print(f"  [{completed}/{len(targets)}] Fingerprinted: {track_name}")
                elif status == "would-fingerprint":
                    fingerprinted += 1
                elif status == "failed":
                    failed += 1

                processed_since_checkpoint += 1
                if processed_since_checkpoint >= CHECKPOINT_EVERY:
                    checkpoint()
                    processed_since_checkpoint = 0

                if completed % 100 == 0:
                    elapsed = time.time() - start_time
                    rate = completed / elapsed if elapsed > 0 else 0
                    remaining = (len(targets) - completed) / rate if rate > 0 else 0
                    print(f"  [progress] {completed}/{len(targets)} scanned "
                          f"({fingerprinted} fingerprinted, {already_tagged} already tagged, "
                          f"{failed} failed) - ~{remaining/60:.1f} min remaining")
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user - flushing progress to CSV before exit.")

    checkpoint()

    elapsed = time.time() - start_time
    print("\n" + "=" * 70)
    print(" BACKFILL COMPLETE")
    print("=" * 70)
    print(f" Already tagged (skipped fpcalc): {already_tagged}")
    print(f" Newly fingerprinted:             {fingerprinted}")
    print(f" Failed:                          {failed}")
    print(f" Elapsed:                         {elapsed/60:.1f} min")
    print("=" * 70)

    if args.dry_run:
        print("[!] DRY_RUN - no tags written, no CSV changes made.")
        return

    if AUTO_GIT_PUSH and fingerprinted > 0:
        try:
            print("\n[*] Committing fingerprint backfill to Git...")
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            with git_operation_lock(timeout=60):
                res_branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root,
                                             capture_output=True, text=True, check=True)
                branch_name = res_branch.stdout.strip()
                subprocess.run(["git", "add", "configs/fmp_data_7718.csv"], cwd=repo_root, check=True,
                                capture_output=True)
                msg = f"Auto-Backfill: Added {fingerprinted} audio fingerprints"
                subprocess.run(["git", "commit", "configs/fmp_data_7718.csv", "-m", msg], cwd=repo_root,
                                check=True, capture_output=True)
                git_safe_pull(branch_name, cwd=repo_root)
                subprocess.run(["git", "push", "origin", branch_name], cwd=repo_root, check=True,
                                capture_output=True)
            print("  [OK] Pushed changes successfully to GitHub.")
        except Exception as e:
            print(f"  [-] Git push failed or skipped: {e}")


if __name__ == "__main__":
    main()
