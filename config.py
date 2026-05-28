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
# Using Firefox to bypass Chromium App-Bound Encryption DPAPI locks
SOMEDL_CMD = ["somedl", "--cookies-from-browser", "firefox"] 
YT_DLP_CMD = ["yt-dlp", "--cookies-from-browser", "firefox"]

# --- API KEYS ---
ACOUSTID_API_KEY = os.getenv("ACOUSTID_API_KEY")
MUSICBRAINZ_USERAGENT = ("FMP_Ultimate_AutoTagger", "1.0", "formypeopleinfo@gmail.com")

# --- SYNC CONFIGS ---
# Toggle Auto-Git Synchronization on successful vaults.
# Keep False by default to respect Git save points.
AUTO_GIT_PUSH = True