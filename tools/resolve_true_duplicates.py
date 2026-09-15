"""
One-time resolution of the 13 "likely true duplicate" pairs found by
tools/fingerprint_dedup.py (logs/fingerprint_dupes.txt), per explicit user
direction 2026-09-15:

- Justin Timberlake "Medley..."/"My Love (Single Version)" and James Brown
  "Please Please Please"/"(single version)": delete both sides, no
  replacement.
- Every other duplicate cluster: delete ALL sides, then queue one fresh
  download per song through the now-fixed ingest pipeline (real bitrate
  measurement, iTunes/Deezer-verified Explicit, Pool auto-assignment,
  self-healing BPM analysis) rather than trust either existing copy.

Parses fingerprint_dupes.txt directly instead of hand-transcribing paths
(several involve non-ASCII punctuation - curly quotes, a Unicode hyphen in
"T‐Pain" - that are easy to get subtly wrong by hand). Groups the 13 pairs
into connected clusters first (Pretty Ricky's 3 pairs are one 3-file
cluster, not 3 independent deletions) via union-find.

Deletion goes through modules.storage.VaultManager.scrub_track(), the
existing, already-comprehensive deletion path (FTP, local + remote
Broadcaster DB over SSH with a properly parameterized query, local G:
drive file, remote Google Drive) - not reimplemented here.

Re-download goes through the live local Ultimate service's own /add
endpoint (the same entry point the DJ portal and redownload tool use),
so it runs through the real, now-corrected pipeline end to end rather
than a script bypassing it.
"""
import os
import sys
import re
import json
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
from modules.storage import VaultManager

REPORT_PATH = os.path.join(BASE_DIR, "logs", "fingerprint_dupes.txt")
ADD_URL = "http://127.0.0.1:58000/add"

# Song key -> (delete_only, clean search query for redownload)
DELETE_ONLY_KEYS = {"justin timberlake", "james brown"}

# Hand-curated clean search query per cluster (safer than auto-stripping
# version tags for only 9 songs) -- matched against each cluster by the
# artist substring found in its member track names.
REDOWNLOAD_QUERIES = {
    "bow wow": "Bow Wow feat. T-Pain & Johnta Austin - Outta My System",
    "chris brown": "Chris Brown - Poppin'",
    "d'angelo": "D'Angelo - Brown Sugar",
    "destiny's child": "Destiny's Child - Soldier feat. T.I. & Lil Wayne",
    "jamie foxx": "Jamie Foxx - Can I Take U Home",
    "jodeci": "Jodeci - Come and Talk to Me (Remix)",
    "pretty ricky": "Pretty Ricky - On the Hotline",
    "pussycat dolls": "Pussycat Dolls - Buttons",
    "usher": "Usher - Nice & Slow",
}


def log(msg):
    print(msg, flush=True)


def parse_true_duplicates(report_path):
    """Returns a list of (track_name, file_path) tuples, only from the
    '### LIKELY TRUE DUPLICATES ###' section of the report."""
    with open(report_path, encoding="utf-8") as f:
        text = f.read()

    section_match = re.search(
        r"### LIKELY TRUE DUPLICATES.*?###.*?\n\n(.*?)\n\n### LEGITIMATE VERSION PAIRS",
        text, re.DOTALL,
    )
    if not section_match:
        raise RuntimeError("Could not find the LIKELY TRUE DUPLICATES section in the report")
    section = section_match.group(1)

    pairs = []
    blocks = section.strip().split("\n\n")
    for block in blocks:
        lines = [l for l in block.splitlines() if l.strip()]
        entry = {}
        for line in lines:
            m = re.match(r"\s*A: (.+)", line)
            if m:
                entry["a_name"] = m.group(1).strip()
                continue
            m = re.match(r"\s*B: (.+)", line)
            if m:
                entry["b_name"] = m.group(1).strip()
                continue
            if line.strip().startswith("G:") or line.strip().startswith("/"):
                if "a_path" not in entry:
                    entry["a_path"] = line.strip()
                else:
                    entry["b_path"] = line.strip()
        if all(k in entry for k in ("a_name", "a_path", "b_name", "b_path")):
            pairs.append(((entry["a_name"], entry["a_path"]), (entry["b_name"], entry["b_path"])))
    return pairs


def to_csv_relative(abs_path):
    """G:\\My Drive\\FMP MUSIC\\BASE\\MUSIC\\Music\\X.mp3 -> Music/X.mp3"""
    marker = "BASE\\MUSIC\\"
    idx = abs_path.find(marker)
    if idx == -1:
        return abs_path.replace("\\", "/")
    return abs_path[idx + len(marker):].replace("\\", "/")


def cluster_pairs(pairs):
    """Union-find over file paths so a 3-way match (Pretty Ricky) becomes
    one 3-file cluster instead of 3 separate pair-deletions."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    names = {}
    for (a_name, a_path), (b_name, b_path) in pairs:
        a_rel, b_rel = to_csv_relative(a_path), to_csv_relative(b_path)
        names[a_rel] = a_name
        names[b_rel] = b_name
        union(a_rel, b_rel)

    clusters = {}
    for path in names:
        root = find(path)
        clusters.setdefault(root, []).append(path)
    return [sorted(members) for members in clusters.values()], names


def classify_cluster(members, names):
    text = " ".join(names[m] for m in members).lower()
    for key in DELETE_ONLY_KEYS:
        if key in text:
            return "delete_only", None
    for key, query in REDOWNLOAD_QUERIES.items():
        if key in text:
            return "delete_and_redownload", query
    return "unknown", None


def trigger_redownload(query):
    payload = {"urls": query, "target": ""}
    req = urllib.request.Request(
        ADD_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        return {"status": "error", "message": str(e)}


def main():
    pairs = parse_true_duplicates(REPORT_PATH)
    log(f"[*] Parsed {len(pairs)} true-duplicate pairs from the report.")

    clusters, names = cluster_pairs(pairs)
    log(f"[*] Grouped into {len(clusters)} duplicate clusters.\n")

    vm = VaultManager()
    redownload_queue = []

    for members in clusters:
        kind, query = classify_cluster(members, names)
        log(f"--- Cluster ({kind}) ---")
        for m in members:
            log(f"    {names[m]}  |  {m}")

        if kind == "unknown":
            log("    [SKIP] Could not classify this cluster against the known song list -- not touching it.\n")
            continue

        for m in members:
            # auto_sync=False: this loop can delete 20+ files in one run.
            # Letting each call fire its own background git-sync thread means
            # that many threads all fighting over one cross-process git lock
            # at once -- confirmed live 2026-09-15, 23 concurrent scrub_track()
            # calls produced dozens of "Could not acquire git lock" warnings
            # and left several deletions never actually committed. One sync
            # after the whole batch (below) instead.
            ok, msg = vm.scrub_track(m, auto_sync=False)
            log(f"    [{'OK' if ok else 'FAIL'}] scrub_track({m!r}) -> {msg}")

        if kind == "delete_and_redownload":
            redownload_queue.append(query)
        log("")

    log("=" * 70)
    log(" All deletions done. Running a single git sync for the whole batch...")
    log("=" * 70)
    vm._git_auto_push("batch duplicate cleanup")

    log("=" * 70)
    log(" Queuing redownloads...")
    log("=" * 70)
    for query in redownload_queue:
        result = trigger_redownload(query)
        log(f"  [{result.get('status', '?')}] {query} -> {result}")
        time.sleep(1)


if __name__ == "__main__":
    main()
