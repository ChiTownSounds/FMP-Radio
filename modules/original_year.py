"""
Original release year lookup (MusicBrainz) for the ingest pipeline.

Problem this solves: the year in a downloaded file's ID3 tag is very often a RE-RELEASE or
compilation date (e.g. 20170209 for a 2002 song). The era pool (Classics / Old School /
Throwbacks / New School) is derived from that year, so old songs land in the wrong pool.

resolve_release_year() prefers MusicBrainz's FIRST release date when it is earlier than the tag's
year, and otherwise keeps the tag - so a failed/uncertain lookup never makes things worse.

Stdlib only. Thread-safe (downloads can run in parallel). Cached in cache/mb_year_cache.json.
The same matching logic lives in FMP_Broadcaster/tools/year_audit.py (the read-only audit) -
keep the two in step.
"""
import os, re, json, time, threading, logging, urllib.request, urllib.parse

UA = 'FMPUltimateIngest/1.0 ( https://fmpmediagroup.com )'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(ROOT, 'cache', 'mb_year_cache.json')
MIN_INTERVAL = 1.1        # MusicBrainz allows ~1 request/second
_lock = threading.Lock()
_last_request = [0.0]
_cache = None

BAD_DISAMBIG = re.compile(r'live|remix|instrumental|karaoke|acapella|a cappella|demo|edit|cover|tribute|version', re.I)


def _norm(s):
    s = (s or '').lower()
    s = re.sub(r'\((?:clean|explicit|radio edit|radio version|album version|single version|remaster(?:ed)?[^)]*)\)', ' ', s)
    s = re.sub(r'\[.*?\]', ' ', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _primary_artist(a):
    a = re.split(r'\s+(?:feat\.?|ft\.?|featuring|with|&|and|x)\s+|,|/|;', a or '', maxsplit=1, flags=re.I)[0]
    return a.strip()


def _clean_title(t):
    t = re.sub(r'\((?:feat|ft|featuring)[^)]*\)', '', t or '', flags=re.I)
    t = re.sub(r'\((?:clean|explicit|radio edit|radio version|album version|single version|remaster(?:ed)?[^)]*)\)', '', t, flags=re.I)
    return re.sub(r'\s+', ' ', t).strip()


def _load_cache():
    global _cache
    if _cache is None:
        try:
            with open(CACHE_PATH, 'r', encoding='utf-8') as f:
                _cache = json.load(f)
        except Exception:
            _cache = {}
    return _cache


def _save_cache():
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(_cache, f)
        os.replace(tmp, CACHE_PATH)
    except Exception:
        pass


def _mb_get(url, timeout=10, tries=2):
    for attempt in range(tries):
        with _lock:
            wait = MIN_INTERVAL - (time.time() - _last_request[0])
            if wait > 0:
                time.sleep(wait)
            _last_request[0] = time.time()
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                time.sleep(3 * (attempt + 1))
                continue
            return None
        except Exception:
            time.sleep(1)
    return None


def lookup_original_year(artist, title, dur_ms=None):
    """dict(mb_year, mb_date, matches, confidence('high'|'medium'|'none'), note). Cached; never raises."""
    try:
        a, t = _primary_artist(artist), _clean_title(title)
        key = _norm(a) + '::' + _norm(t)
        with _lock:
            cache = _load_cache()
            if key in cache and cache[key].get('note') != 'lookup failed':
                return cache[key]
        q = f'recording:"{t}" AND artist:"{a}"'
        data = _mb_get('https://musicbrainz.org/ws/2/recording/?' + urllib.parse.urlencode({'query': q, 'fmt': 'json', 'limit': 25}))
        if data is None:
            return {'mb_year': None, 'mb_date': '', 'matches': 0, 'confidence': 'none', 'note': 'lookup failed'}  # not cached
        tn, an = _norm(t), _norm(a)
        good = []
        for rec in data.get('recordings', []):
            if rec.get('score', 0) < 90 or _norm(rec.get('title')) != tn:
                continue
            credit = ' '.join(ac.get('name', '') for ac in rec.get('artist-credit', []) if isinstance(ac, dict))
            if an not in _norm(credit) or BAD_DISAMBIG.search(rec.get('disambiguation', '') or ''):
                continue
            d = rec.get('first-release-date') or ''
            m = re.match(r'(\d{4})', d)
            if not m:
                continue
            length = rec.get('length')
            if dur_ms and length and abs(length - dur_ms) > 20000:
                continue
            good.append((int(m.group(1)), d))
        if not good:
            res = {'mb_year': None, 'mb_date': '', 'matches': 0, 'confidence': 'none', 'note': 'no clean match'}
        else:
            good.sort()
            by, bd = good[0]
            agree = sum(1 for y, _ in good if y == by)
            res = {'mb_year': by, 'mb_date': bd, 'matches': len(good),
                   'confidence': 'high' if agree >= 2 or (len(good) == 1 and len(bd) >= 7) else 'medium', 'note': ''}
        with _lock:
            _load_cache()[key] = res
            _save_cache()
        return res
    except Exception as e:  # a lookup problem must never break a download
        logging.warning(f'[original_year] lookup error: {e}')
        return {'mb_year': None, 'mb_date': '', 'matches': 0, 'confidence': 'none', 'note': 'error'}


def resolve_release_year(tag_year, artist, title, dur_ms=None):
    """
    Return (year_string, source). Keeps the tag's year unless MusicBrainz confidently reports an
    EARLIER original release (the safe direction: re-releases are always later than the original).
    source is 'tag', 'musicbrainz' or 'Unknown'.
    """
    ty = None
    m = re.search(r'(\d{4})', str(tag_year or ''))
    if m and 1900 <= int(m.group(1)) <= 2100:
        ty = int(m.group(1))
    if not artist or not title or str(artist).lower().startswith('unknown'):
        return (str(ty) if ty else 'Unknown'), ('tag' if ty else 'Unknown')
    mb = lookup_original_year(artist, title, dur_ms)
    my = mb.get('mb_year')
    if my and mb.get('confidence') in ('high', 'medium') and 1900 <= my <= 2100:
        if ty is None or my < ty:
            logging.info(f'[original_year] {artist} - {title}: tag={ty} -> MusicBrainz first release {my}')
            return str(my), 'musicbrainz'
    return (str(ty) if ty else 'Unknown'), ('tag' if ty else 'Unknown')
