# Inputs — how to paste IDs and ZIPs

## Item IDs

File: `inputs/item_ids.txt`

```
12368855112
12322622968
5022241681
```

Also accepts CSV if path ends with `.csv` and has column `item_id`.

## ZIP codes

File: `inputs/zip_codes.txt`

All ZIP 1 / ZIP 2 / ZIP 3 values from the user's table were flattened into unique lines (one ZIP per line).

Also accepts CSV with column `zip_code`.

## Config switch

In `config.py`:

```python
ITEM_IDS_FILE = "inputs/item_ids.txt"
ZIP_CODES_FILE = "inputs/zip_codes.txt"
```
