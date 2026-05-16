import os

# --- PATHS ---
STAGING_DIR = "C:/fmp_ultimate/staging"
CSV_BLUEPRINT = "C:/FMP_Ultimate/configs/fmp_data_7718.csv"

# --- FTP SERVER SETTINGS ---
# Using Citrus3 Custom Port 2121
FTP_HOST = "hello.citrus3.com"
FTP_PORT = 2121
FTP_USER = "ftp_1047"
FTP_PASS = "EC6V7bQ!$CQs"
# Citrus3 drops the user directly into the root media folder
FTP_BASE_DIR = "/" 

# --- EXECUTABLES ---
SOMEDL_CMD = ["somedl"] 
YT_DLP_CMD = ["yt-dlp"]

# --- API KEYS ---
ACOUSTID_API_KEY = "cc5tCw5q9G" 
MUSICBRAINZ_USERAGENT = ("FMP_Ultimate_AutoTagger", "1.0", "formypeopleinfo@gmail.com")