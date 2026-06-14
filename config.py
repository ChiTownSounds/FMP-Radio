import os
from dotenv import load_dotenv

# Load secrets from the .env file
load_dotenv()

# --- PATHS ---
# Using dynamic relative paths to prevent sandbox crashes
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STAGING_DIR = os.path.join(BASE_DIR, "staging")
CSV_BLUEPRINT = os.path.join(BASE_DIR, "configs", "fmp_data_7718.csv")

# --- FTP SERVER SETTINGS ---
# Loaded securely from .env
FTP_HOST = os.getenv("FTP_HOST", "hello.citrus3.com")
FTP_PORT = int(os.getenv("FTP_PORT", 2121))
FTP_USER = os.getenv("FTP_USER")
FTP_PASS = os.getenv("FTP_PASS")
# Citrus3 drops the user directly into the root media folder
FTP_BASE_DIR = "/" 

# --- EXECUTABLES ---
# Using Firefox to bypass Chromium App-Bound Encryption DPAPI locks.
# If a manual cookies.txt file is placed in configs/, prioritize it (essential for headless servers/OCI).
COOKIES_FILE = os.path.join(BASE_DIR, "configs", "cookies.txt")
if os.path.exists(COOKIES_FILE):
    SOMEDL_CMD = ["somedl", "--cookies", COOKIES_FILE]
    YT_DLP_CMD = ["yt-dlp", "--cookies", COOKIES_FILE]
else:
    SOMEDL_CMD = ["somedl", "--cookies-from-browser", "firefox"] 
    YT_DLP_CMD = ["yt-dlp", "--cookies-from-browser", "firefox"]

# --- API KEYS ---
ACOUSTID_API_KEY = os.getenv("ACOUSTID_API_KEY")
MUSICBRAINZ_USERAGENT = ("FMP_Ultimate_AutoTagger", "1.0", "formypeopleinfo@gmail.com")

# --- SYNC CONFIGS ---
# Toggle Auto-Git Synchronization on successful vaults.
# Keep False by default to respect Git save points.
AUTO_GIT_PUSH = True

# --- iHEART SYNC SETTINGS ---
IHEART_SYNC_ENABLED = True
IHEART_STATION_ID = "865"  # WVAZ V103 Chicago
IHEART_POLL_INTERVAL = 30  # seconds
IHEART_CHURCH_FOLDER = "Shows/InspirationalChurch"
IHEART_CHURCH_DAYS = [6]  # Sunday (6 in datetime.date.weekday())
IHEART_CHURCH_START_HOUR = 5  # 5:00 AM
IHEART_CHURCH_END_HOUR = 13  # 1:00 PM
IHEART_CHURCH_KEYWORDS = ["gospel", "choir", "worship", "praise", "pastor", "bishop", "jesus", "god", "lord", "christ", "hymn", "spiritual"]

# --- NON-SONG EXCLUSION LOGIC ---
def is_non_song(track_name, file_path):
    path_lower = file_path.lower() if file_path else ""
    name_lower = track_name.lower() if track_name else ""
    
    # Directory/Path keywords
    non_song_dirs = ['ondemand', 'sweeper', 'promo', 'drop', 'commercial', 'sfx', 'effect', 'liner', 'branding', 'shows', 'adbreak', 'ad break']
    if any(x in path_lower for x in non_song_dirs):
        return True
        
    # Track Name keywords
    non_song_names = ['sweeper', 'chicago l announcement', 'liner', 'celebrity drop', 'fmp radio', 'ad break', 'commercial', 'sfx']
    if any(x in name_lower for x in non_song_names):
        return True
        
    return False