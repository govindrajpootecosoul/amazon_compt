# Adding a new Amazon region

## Short answer

**No** — only creating `inputs/xx_asins.txt` is **not** enough.

The region must also be registered in `amazon/config.py` → `MARKETPLACES`.

## Already supported

| Code | Site | ASIN file |
|------|------|-----------|
| us | amazon.com | `inputs/us_asins.txt` |
| uk | amazon.co.uk | `inputs/uk_asins.txt` |
| de | amazon.de | `inputs/de_asins.txt` |
| ca | amazon.ca | `inputs/ca_asins.txt` |
| ae | [amazon.ae](https://www.amazon.ae/) | `inputs/ae_asins.txt` |

## Run UAE

```bash
cd amazon
python scraper.py ae
```

## To add another region later (e.g. amazon.in / amazon.com.au)

1. Create `inputs/in_asins.txt` (ASINs)
2. In `config.py` add under `MARKETPLACES`, `PROXIES`, `DELIVERY_POSTAL`:

```python
"in": {
    "name": "India",
    "domain": "www.amazon.in",
    "currency": "INR",
    "locale": "en-IN",
    "timezone_id": "Asia/Kolkata",
    "accept_language": "en-IN,en;q=0.9",
    "asin_file": "inputs/in_asins.txt",
    "output_file": "output/amazon_in_data.xlsx",
    "proxy_country": "IN",
},
```

3. Run: `python scraper.py in`

`python scraper.py all` scrapes **every** marketplace listed in `MARKETPLACES`.
