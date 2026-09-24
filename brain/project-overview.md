# Project overview

Two scrapers in this repo:

1. **Walmart** (project root) — Item ID × ZIP
2. **Amazon** (`amazon/`) — ASIN by marketplace (us / uk / de / ca)

## Walmart

Uses Google Chrome (not headless Chromium) because Walmart serves a PerimeterX "Robot or human?" page to headless browsers. HTTP 200 does not mean product data loaded.

### Inputs

- `inputs/item_ids.txt`
- `inputs/zip_codes.txt`

### Outputs

- `output/walmart_scraped_data.xlsx` — same columns every run
- `browser_profile/` — persisted Chrome session (do not delete while scraping)

### Main entry

`python scraper.py`

## Amazon

See `brain/amazon-scraper.md`. Folder: `amazon/`. Run: `python amazon/scraper.py us`

## Why Excel was empty

Previous run hit `title: Robot or human?` / `px-captcha`. Script treated it as a product page and saved empty columns. Extraction now waits for `__NEXT_DATA__` / JSON-LD and only marks Success when title is present.
