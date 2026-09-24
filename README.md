# Walmart Scraper

Headless Python scraper for Walmart item pages across many ZIP codes.

## How to pass Item IDs and ZIP codes

Paste into these files (one value per line). You can also paste space/comma separated values on one line.

| What | File |
|------|------|
| Walmart Item IDs | `inputs/item_ids.txt` |
| ZIP codes | `inputs/zip_codes.txt` |

Optional: CSV with columns `item_id` / `zip_code` — change paths in `config.py`.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

## Run (important — captcha fix)

Playwright Chrome is **detected** → "Press & Hold" shows **Please try again**.

**Do this instead:**

1. Double-click **`start_chrome.bat`**
2. In that Chrome, open https://www.walmart.com/ and pass the captcha
3. Run **`python scraper.py`** and press ENTER when homepage loads

Output: `output/walmart_data.xlsx`

Close Excel before running. After one good full run, set `FORCE_RESCRAPE_ALL = False` in `config.py`.


## Config (`config.py`)

- `PROXY_SERVER` — residential proxy URL (recommended for large runs)
- `HEADLESS = True` — run in background
- `MAX_ZIPS` / `MAX_ITEMS` — limit for test runs (set `2` / `3` first)
- Delay settings — increase if you see 403 / captcha

## IP safety

Walmart uses PerimeterX. Without a residential proxy, heavy scraping from your home IP often gets blocked. Set `PROXY_SERVER` in `config.py` before full runs.
