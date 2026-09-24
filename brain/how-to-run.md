# How to run

1. Install Google Chrome (required — Walmart blocks headless Chromium).
2. `pip install -r requirements.txt`
3. `python -m playwright install chromium`
4. Paste Item IDs into `inputs/item_ids.txt`
5. Paste ZIP codes into `inputs/zip_codes.txt`
6. Optional: set `PROXY_SERVER` in `config.py` (US residential)
7. Optional test: set `MAX_ZIPS = 1` and `MAX_ITEMS = 1` in `config.py`
8. Run: `python scraper.py`
9. Results: `output/walmart_scraped_data.xlsx`

Failed rows (empty title/price) are retried on the next run. Successful rows are skipped.
