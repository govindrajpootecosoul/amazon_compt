# Amazon price / delivery location notes

## Why some prices were blank

Availability showed:
`This item cannot be shipped to your selected delivery location...`

Cause: scraping Amazon.com from India — delivery location was India/non-US, so Amazon hid the buy-box price.

Fix: scraper now sets `DELIVERY_POSTAL` (US default `10001`) via Amazon "Deliver to" UI after warmup.

## Why some prices looked wrong (952.38, 1,14)

- `1,14` = EU-style decimal comma → now normalized to `1.14`
- `952.38` = whole+fraction concat bug → now reads `a-offscreen` or `whole.fraction` properly

## Re-run

`FORCE_RESCRAPE_ALL = True` once so old Excel rows are overwritten. After a good run, set it back to `False`.
