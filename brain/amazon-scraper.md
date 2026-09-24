# Amazon scraper — slow mode (no proxy, from India)

## How to run

1. Close all Chrome windows
2. Double-click `amazon/start_chrome.bat`
3. Pass captcha if shown
4. Terminal:

```bash
cd c:\govind\walmartscrapper\amazon

# One marketplace
python scraper.py us

# ALL marketplaces in ONE command (US → UK → DE → CA)
python scraper.py all
```

5. Press ENTER when each Amazon homepage is ready (warmup)
6. Excel goes to a date-time folder, e.g.:

```
output/2026-09-11_100800/
  amazon_us_data.xlsx
  amazon_uk_data.xlsx
  amazon_de_data.xlsx
  amazon_ca_data.xlsx
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
