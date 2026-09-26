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


# ---------------------------------------------------------------------------
# Song-level genre from the iTunes Search API (free, no key).
# Why: MusicBrainz genres are ARTIST-level, so soul singers with gospel roots
# (Aretha, Al Green, Sam Cooke, Billy Preston, Gregory Porter) all carry a
# "gospel" tag and their secular hits were being routed to the Gospel pool,
# while many real gospel artists (Pastor Mike Jr., Chandler Moore, Todd Dulaney,
# Travis Greene) have no MusicBrainz genres at all. Apple files each SONG under
# a genre ("Christian", "Contemporary Gospel", "R&B/Soul", "Hip-Hop/Rap"), which
# got 30 of 34 real station cases right (2026-09-25) vs 10 of 28 before.
# ---------------------------------------------------------------------------
_SONG_CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "configs", "song_genre_cache.json")
_last_itunes_request = 0.0


def _song_key(s):
    import unicodedata
    s = ''.join(c for c in unicodedata.normalize('NFKD', s or '') if not unicodedata.combining(c)).lower()
    return re.sub(r'[^a-z0-9]', '', re.sub(r'[\(\[].*?[\)\]]', '', s))


def _main_artist(a):
    return re.split(r'\s+(?:feat\.?|featuring|ft\.?|&|and|x|with)\s+|,', a or '', flags=re.I)[0].strip()


def get_song_genre(artist: str, title: str):
    """Apple Music's genre for this exact song (e.g. 'Christian', 'R&B/Soul'), or None when iTunes has no
    confident match (same main artist, same title). Cached on disk; lookup failures are NOT cached."""
    global _last_itunes_request
    a_key, t_key = _song_key(_main_artist(artist)), _song_key(title)
    if not a_key or not t_key:
        return None
    key = f"{a_key}|{t_key}"
    with _cache_lock:
        cache = {}
        if os.path.exists(_SONG_CACHE_PATH):
            try:
                with open(_SONG_CACHE_PATH, "r", encoding="utf-8") as f:
                    cache = json.load(f)
            except Exception:
                cache = {}
        if key in cache:
            return cache[key]
        # iTunes allows roughly 20 searches a minute
        elapsed = time.time() - _last_itunes_request
        if elapsed < 3.2:
            time.sleep(3.2 - elapsed)
        _last_itunes_request = time.time()
    try:
        term = f"{_main_artist(artist)} {re.sub(r'[(].*', '', title or '').strip()}"
        url = "https://itunes.apple.com/search?" + urllib.parse.urlencode({"term": term, "entity": "song", "limit": 15, "country": "US"})
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "FMP-Ultimate/1.0"}), timeout=15) as res:
            data = json.loads(res.read().decode("utf-8"))
    except Exception as e:
        logging.debug(f"[iTunes] Song genre lookup failed for '{artist} - {title}': {e}")
        return None
    genre = None
    for r in data.get("results", []):
        r_title, r_artist = _song_key(r.get("trackName")), _song_key(r.get("artistName"))
        if (r_title == t_key or t_key in r_title) and a_key[:5] in r_artist:
            genre = r.get("primaryGenreName")
            break
    with _cache_lock:
        try:
            cache = {}
            if os.path.exists(_SONG_CACHE_PATH):
                with open(_SONG_CACHE_PATH, "r", encoding="utf-8") as f:
                    cache = json.load(f)
            cache[key] = genre
            with open(_SONG_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2)
        except Exception as e:
            logging.error(f"[iTunes] Failed to save song genre cache: {e}")
    return genre


def is_gospel_genre(genre: str) -> bool:
    g = (genre or "").lower()
    return any(t in g for t in ("gospel", "christian", "worship", "inspirational"))
