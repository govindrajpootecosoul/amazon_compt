# Buybox owner empty / wrong (esp. amazon.ae)

## Column

`buybox_owner` — merchant from **Sold by** (renamed from `buybox_seller`).

## Past issues

- Blank owner while ships/fulfilled only showed `Delivered by`
- `Details` / `See less` UI text saved instead of merchant name
- Parallel scrape: winners with price but blank `buybox_owner` / `buybox_ships_from`
- Garbled owners: `Day To Day … Day To Day … Sold by Day To Day …`

## Fix summary (2026-09)

- `_dedupe_seller_phrase` + stronger `_clean_seller_name` (collapse repeats / Sold by noise)
- JS `cleanLabelValue` / AOD `clean` mirror the same dedupe
- Prefer `#sellerProfileTriggerId` **before** tabular text (avoids concatenated junk)
- Wait ~0.45s for merchant block before extract (parallel race)
- Blank winners repaired from same-price AOD row, then pinned AOD card
- Price match uses `_norm_price_key` (`40.8` == `40.80`)
- **Do not** invent `Amazon.ae` for every blank winner (mislabels 3P)
- Amazon owner only when ships/fulfilled/page text clearly Amazon-sold

Re-run: `python amazon/scraper.py ae`
