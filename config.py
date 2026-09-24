"""
Walmart scraper configuration.
"""

# --- Input / Output paths ---
ITEM_IDS_FILE = "inputs/item_ids.txt"
ZIP_CODES_FILE = "inputs/zip_codes.txt"
OUTPUT_FILE = "output/walmart_data.xlsx"
CHECKPOINT_FILE = "output/checkpoint.json"

# --- Proxy ---
PROXY_SERVER = None

# --- Browser mode ---
CONNECT_EXISTING_CHROME = True
AUTO_LAUNCH_CHROME = True
CHROME_DEBUG_PORT = 9222
CHROME_DEBUG_URL = f"http://127.0.0.1:{CHROME_DEBUG_PORT}"

HEADLESS = False
USE_SYSTEM_CHROME = True
HIDE_BROWSER_WINDOW = False
USER_DATA_DIR = "browser_profile"
SLOW_MO_MS = 0

MANUAL_WARMUP = True
CAPTCHA_WAIT_SECONDS = 180
MAX_RETRIES_PER_ITEM = 3

# --- Rate limiting ---
DELAY_BETWEEN_ITEMS_MIN = 3.0
DELAY_BETWEEN_ITEMS_MAX = 6.0
DELAY_BETWEEN_ZIPS_MIN = 5.0
DELAY_BETWEEN_ZIPS_MAX = 10.0
BLOCK_COOLDOWN_SECONDS = 20

# --- Limits (None = all) ---
MAX_ZIPS = None
MAX_ITEMS = None

# Skip only rows that have correct fulfillment ETAs.
RESUME_FROM_CHECKPOINT = True

# True = ignore old Excel rows and scrape everything again (recommended once).
FORCE_RESCRAPE_ALL = True

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]
