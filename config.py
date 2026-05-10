from pathlib import Path

# --- CORE DIRECTORIES ---
BASE_DIR = Path(__file__).resolve().parent 

CONFIG_DIR = BASE_DIR / "configs"
MODULES_DIR = BASE_DIR / "modules"
STAGING_DIR = BASE_DIR / "staging"

# --- THE SOURCE OF TRUTH ---
CSV_BLUEPRINT = CONFIG_DIR / "fmp_data_7718.csv"
SERVER_DIR = Path("Z:\\")

# --- EXTERNAL TOOLS (DUAL-ENGINE ARCHITECTURE) ---
# Engine 1: The Transporter (Downloads & tags using YouTube Music / Genius API)
SOMEDL_CMD = ["python", "-m", "SomeDL.main"]

# Engine 2: The Probe (Strictly for scanning JSON metadata at the door)
YT_DLP_CMD = ["yt-dlp"]