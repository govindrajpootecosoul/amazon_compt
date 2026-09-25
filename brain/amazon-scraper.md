# Amazon scraper — slow mode (no proxy)

## How to run

1. Quit all Chrome windows
2. Start debug Chrome:
   - **Windows:** double-click `amazon/start_chrome.bat`
   - **Mac:** `chmod +x amazon/start_chrome.sh && ./amazon/start_chrome.sh`
3. Pass captcha if shown
4. Terminal:

```bash
cd amazon

# One marketplace
python scraper.py us

# ALL marketplaces (US → UK → DE → CA → AE)
python scraper.py all
```

5. Press ENTER when each Amazon homepage is ready (warmup)
6. Excel goes to a date-time folder, e.g.:

```
output/2026-09-11_100800/
  amazon_us_data.xlsx
  amazon_uk_data.xlsx
  ...
```

## Config highlights (`amazon/config.py`)

- `REQUIRE_PROXY = False`
- `CONNECT_EXISTING_CHROME = True`
- `MANUAL_WARMUP = True`
- `NEW_FILE_EACH_RUN = True`
- `DELAY_BETWEEN_ASINS` = 2–4s (faster; raise if blocked)
- `PAGE_SETTLE_SECONDS` = 0.5
- `MAX_RETRIES_PER_ASIN` = 1
- Auto-stop after 3 consecutive blocks
