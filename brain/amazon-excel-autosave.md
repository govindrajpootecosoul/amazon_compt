# Amazon Excel autosave

## What happened

Scraper saves after **every ASIN** into `amazon/output/amazon_us_data.xlsx`.

If that file is **open in Excel**, Windows locks it → Permission denied → scraper used to write a **new** `amazon_us_data_autosave_TIMESTAMP.xlsx` each time → many files.

## Fix

- Close Excel while scraping (best).
- Code now reuses **one** file: `amazon_us_data_autosave.xlsx` (overwrites), no timestamp spam.
- Old `*_autosave_YYYYMMDD_*.xlsx` files can be deleted; keep the latest data in `amazon_us_data.xlsx` or the single autosave.
