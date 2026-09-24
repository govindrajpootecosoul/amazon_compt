# Excel PermissionError fix

## Error

```
PermissionError: [Errno 13] Permission denied: 'output\walmart_scraped_data.xlsx'
```

## Cause

The Excel file was **open in Microsoft Excel / Cursor / another app** while the scraper tried to overwrite it. Windows locks open `.xlsx` files.

## Fix for user

1. **Close** `walmart_scraped_data.xlsx` in Excel
2. Run `python scraper.py` again (resume continues from successful rows)

## Script behavior now

- Retries save 3 times
- If still locked, writes `output/walmart_scraped_data_autosave_TIMESTAMP.xlsx`
- Scrape does **not** crash
