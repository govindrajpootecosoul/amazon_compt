# Amazon output + multi buy box

## Output (every run = new file)

Each run creates a date-time folder:

```
amazon/output/2026-09-11_095830/amazon_us_data.xlsx
amazon/output/2026-09-11_101200/amazon_uk_data.xlsx
```

Config (`amazon/config.py`):

- `NEW_FILE_EACH_RUN = True`
- `OUTPUT_FOLDER_BY_DATETIME = True`

## Multiple buy boxes / offers

- Opens "See All Buying Options" / offer listing when available
- One Excel **row per offer**
- Columns: `offer_index`, `offer_count`, `is_buybox_winner`
- If no offers: `buybox_available = no buybox`
