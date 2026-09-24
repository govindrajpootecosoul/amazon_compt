# Amazon inputs

## ASIN lists (geographic)

One ASIN per line. Comma/space-separated on one line also works. Lines starting with `#` are comments.

| Marketplace | File |
|-------------|------|
| US | `amazon/inputs/us_asins.txt` |
| UK | `amazon/inputs/uk_asins.txt` |
| DE | `amazon/inputs/de_asins.txt` |
| CA | `amazon/inputs/ca_asins.txt` |

CSV with an `asin` column is also supported if you point `asin_file` in `amazon/config.py` to a `.csv`.

## Sample US list

User provided initial US ASINs (16) — stored in `amazon/inputs/us_asins.txt`.
