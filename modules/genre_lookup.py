import os
import re
import json
import time
import logging
import threading
import urllib.request
import urllib.parse

_CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "configs", "artist_genre_cache.json")
_cache_lock = threading.Lock()
_last_mb_request = 0.0


def _load_cache():
    if os.path.exists(_CACHE_PATH):
        try:
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_cache(cache):
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        logging.error(f"[MusicBrainz] Failed to save artist genre cache: {e}")


def _mb_request(url):
    # MusicBrainz's courtesy rate limit is ~1 req/sec, shared across every
    # call this process makes (artist search + the follow-up genre lookup).
    global _last_mb_request
    from config import MUSICBRAINZ_USERAGENT
    ua_name, ua_version, ua_contact = MUSICBRAINZ_USERAGENT
    headers = {"User-Agent": f"{ua_name}/{ua_version} ({ua_contact})"}
    with _cache_lock:
        elapsed = time.time() - _last_mb_request
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        _last_mb_request = time.time()
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.loads(res.read().decode("utf-8"))


def get_artist_genres(artist_name: str) -> list:
    """Returns a lowercased list of MusicBrainz artist-level genre/tag names
    for artist_name, using a local on-disk cache keyed by normalized artist
    name. Recording-level tags were checked and found too sparse to be a
    usable signal (verified 2026-09-03 against a real gospel recording with
    empty tags/genres) -- artist-level is the layer worth querying.
    Returns [] on any lookup failure or unknown artist; callers should treat
    that as "no signal", not "definitely not a match" -- failures are never
    cached, so a transient network error gets retried on the next play
    instead of being locked in as a permanent negative.
    """
    if not artist_name:
        return []
    norm_key = re.sub(r'[^a-z0-9]', '', artist_name.lower())
    if not norm_key:
        return []

    cache = _load_cache()
    if norm_key in cache:
        return cache[norm_key]

    try:
        query = urllib.parse.quote(f'artist:"{artist_name}"')
        search_url = f"https://musicbrainz.org/ws/2/artist/?query={query}&fmt=json&limit=1"
        search_data = _mb_request(search_url)
        artists = search_data.get("artists", [])
        genres = []
        if artists:
            mbid = artists[0].get("id")
            if mbid:
                lookup_url = f"https://musicbrainz.org/ws/2/artist/{mbid}?inc=genres&fmt=json"
                artist_data = _mb_request(lookup_url)
                genres = [g.get("name", "").lower() for g in artist_data.get("genres", []) if g.get("name")]
    except Exception as e:
        logging.debug(f"[MusicBrainz] Artist genre lookup failed for '{artist_name}': {e}")
        return []

    cache[norm_key] = genres
    _save_cache(cache)
    return genres
