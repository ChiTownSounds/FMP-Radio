"""
Cross-checks a track's Explicit status against iTunes + Deezer at ingest
time -- both free, auth-free, and both carry a real editorial Explicit flag
assigned by the label, not inferred from a filename or a UI checkbox. This
is the same duration+token-overlap matching discipline the standalone
library accuracy audit (tools/audit_library_accuracy.py) uses, refactored
here so it runs automatically on every future download instead of only
after the fact.

Deliberately conservative for an unattended pipeline: verify_explicit()
only returns a value when BOTH sources confidently match the same
recording AND agree on its explicit status. Any disagreement, missing
match, or ambiguity returns None so the caller keeps whatever it already
had rather than risk auto-setting a wrong flag with nobody watching.
"""
import re
import time
import json
import unicodedata
import urllib.request
import urllib.parse

_last_call = 0.0
REQUEST_PAUSE = 1.1


def _norm_text(t):
    t = (t or "").lower()
    t = unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode('ascii')
    t = re.sub(r'\[.*?\]|\(.*?\)', '', t)
    t = t.replace('&', ' and ')
    return re.sub(r'[^a-z0-9]+', ' ', t).strip()


def _token_overlap(a, b):
    ta, tb = set(_norm_text(a).split()), set(_norm_text(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def _paced_get_json(url):
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < REQUEST_PAUSE:
        time.sleep(REQUEST_PAUSE - elapsed)
    _last_call = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "FMP-Ultimate-Ingest/1.0"})
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.loads(res.read().decode('utf-8'))


def _itunes_lookup(artist, title):
    try:
        q = urllib.parse.quote(f"{artist} {title}")
        data = _paced_get_json(f"https://itunes.apple.com/search?term={q}&media=music&entity=song&limit=5")
        return [{
            "artist": r.get("artistName", ""), "title": r.get("trackName", ""),
            "duration_ms": r.get("trackTimeMillis"), "explicitness": r.get("trackExplicitness"),
        } for r in data.get("results", [])]
    except Exception:
        return []


def _deezer_lookup(artist, title):
    try:
        q = urllib.parse.quote(f"{artist} {title}")
        data = _paced_get_json(f"https://api.deezer.com/search?q={q}&limit=5")
        return [{
            "artist": (r.get("artist") or {}).get("name", ""), "title": r.get("title", ""),
            "duration_ms": (r.get("duration") or 0) * 1000, "explicit_lyrics": r.get("explicit_lyrics"),
        } for r in data.get("data", [])]
    except Exception:
        return []


def _best_match(candidates, title, artist, duration_ms, tol_ms=15000):
    best, best_score = None, 0.0
    for c in candidates:
        if duration_ms and c.get("duration_ms"):
            if abs(c["duration_ms"] - duration_ms) > tol_ms:
                continue
        score = (_token_overlap(c.get("title", ""), title) + _token_overlap(c.get("artist", ""), artist)) / 2
        if score > best_score:
            best_score, best = score, c
    return best if best and best_score >= 0.5 else None


def verify_explicit(artist, title, duration_ms=None):
    """Returns True/False if iTunes and Deezer both confidently match the
    same recording and agree on explicit status, else None."""
    if not artist or not title:
        return None

    it_match = _best_match(_itunes_lookup(artist, title), title, artist, duration_ms)
    dz_match = _best_match(_deezer_lookup(artist, title), title, artist, duration_ms)

    signals = []
    if it_match and it_match.get("explicitness") in ("explicit", "notExplicit", "cleaned"):
        signals.append(it_match["explicitness"] == "explicit")
    if dz_match and dz_match.get("explicit_lyrics") is not None:
        signals.append(bool(dz_match["explicit_lyrics"]))

    if len(signals) < 2 or len(set(signals)) > 1:
        return None
    return signals[0]
