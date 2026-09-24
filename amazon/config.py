"""
Amazon scraper configuration (geographic marketplaces).

NO PROXY mode (India): use real Chrome + pass captcha once.
Delays are tuned for faster scrapes; increase DELAY_* if you get blocked.
"""

# Which marketplace to scrape: us | uk | de | ca | ae | all
# Override from CLI:  python scraper.py us
# New region? Add it under MARKETPLACES + inputs/{code}_asins.txt (not inputs alone).
MARKETPLACE = "us"

# --- Input / Output (paths relative to amazon/ folder) ---
OUTPUT_DIR = "output"
CHECKPOINT_FILE = "output/checkpoint.json"

# Every run creates a NEW Excel under a date-time folder:
#   output/2026-09-11_095830/amazon_us_data.xlsx
NEW_FILE_EACH_RUN = True
OUTPUT_FOLDER_BY_DATETIME = True

# ---------------------------------------------------------------------------
# Proxy (optional — leave None for slow Chrome mode from India)
# Format: "http://USERNAME:PASSWORD@host:port"
# ---------------------------------------------------------------------------
PROXY_SERVER = None

PROXIES = {
    "us": None,
    "uk": None,
    "de": None,
    "ca": None,
    "ae": None,
}

# Allow run without proxy (slow Chrome + manual captcha once)
REQUIRE_PROXY = False

# --- Browser: real Chrome window (best chance without proxy) ---
CONNECT_EXISTING_CHROME = True
AUTO_LAUNCH_CHROME = True
CHROME_DEBUG_PORT = 9223
CHROME_DEBUG_URL = f"http://127.0.0.1:{CHROME_DEBUG_PORT}"

HEADLESS = False
USE_SYSTEM_CHROME = True
HIDE_BROWSER_WINDOW = False
USER_DATA_DIR = "browser_profile"
SLOW_MO_MS = 0

# One-time: pass Amazon captcha in Chrome, then press ENTER in terminal
MANUAL_WARMUP = True
# If captcha appears mid-run, wait so you can solve it in the open Chrome window
CAPTCHA_WAIT_SECONDS = 120
MAX_RETRIES_PER_ASIN = 1

# --- Faster pacing (still not instant — Amazon blocks if too aggressive) ---
DELAY_BETWEEN_ASINS_MIN = 2.0
DELAY_BETWEEN_ASINS_MAX = 4.0
PAGE_SETTLE_SECONDS = 0.5
BLOCK_COOLDOWN_SECONDS = 30
# Stop after this many blocks in a row
MAX_CONSECUTIVE_BLOCKS = 3

# None = scrape all ASINs in the file
MAX_ASINS = None

# Delivery location (ZIP/postal) so buy-box + price show correctly.
# From India, without a US ZIP Amazon often says "cannot be shipped" and hides price.
DELIVERY_POSTAL = {
    "us": "10001",   # New York
    "uk": "SW1A 1AA",
    "de": "10115",
    "ca": "M5H 2N2",
    "ae": "Dubai",   # Amazon.ae uses city / area
}

RESUME_FROM_CHECKPOINT = True
FORCE_RESCRAPE_ALL = False  # keep Success + Skip rows; only retry Failed/Blocked

MARKETPLACES = {
    "us": {
        "name": "United States",
        "domain": "www.amazon.com",
        "currency": "USD",
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "accept_language": "en-US,en;q=0.9",
        "asin_file": "inputs/us_asins.txt",
        "output_file": "output/amazon_us_data.xlsx",
        "proxy_country": "US",
    },
    "uk": {
        "name": "United Kingdom",
        "domain": "www.amazon.co.uk",
        "currency": "GBP",
        "locale": "en-GB",
        "timezone_id": "Europe/London",
        "accept_language": "en-GB,en;q=0.9",
        "asin_file": "inputs/uk_asins.txt",
        "output_file": "output/amazon_uk_data.xlsx",
        "proxy_country": "GB",
    },
    "de": {
        "name": "Germany",
        "domain": "www.amazon.de",
        "currency": "EUR",
        "locale": "de-DE",
        "timezone_id": "Europe/Berlin",
        "accept_language": "de-DE,de;q=0.9,en;q=0.8",
        "asin_file": "inputs/de_asins.txt",
        "output_file": "output/amazon_de_data.xlsx",
        "proxy_country": "DE",
    },
    "ca": {
        "name": "Canada",
        "domain": "www.amazon.ca",
        "currency": "CAD",
        "locale": "en-CA",
        "timezone_id": "America/Toronto",
        "accept_language": "en-CA,en;q=0.9",
        "asin_file": "inputs/ca_asins.txt",
        "output_file": "output/amazon_ca_data.xlsx",
        "proxy_country": "CA",
    },
    "ae": {
        "name": "United Arab Emirates",
        "domain": "www.amazon.ae",
        "currency": "AED",
        "locale": "en-AE",
        "timezone_id": "Asia/Dubai",
        "accept_language": "en-AE,en;q=0.9,ar;q=0.8",
        "asin_file": "inputs/ae_asins.txt",
        "output_file": "output/amazon_ae_data.xlsx",
        "proxy_country": "AE",
    },
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]
