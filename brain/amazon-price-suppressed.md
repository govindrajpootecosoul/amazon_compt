# Price higher than typical (suppressed buy box)

## What Amazon shows

Instead of a normal price + Add to Cart:

- Message: **Price higher than typical** / Learn more  
- Button: **See All Buying Options** only  
- No Ships from / Sold by on the main buy box

## Requirement

Do **not** fill `price` from:

- "4 options from AED …"
- Carousel / sponsored prices
- Other random `.a-price` on the page

## Excel behavior

| Column | Value |
|--------|--------|
| `price` | `price not available` |
| `was_price` | blank |
| `buybox_price` (main, no offers) | blank |
| `buybox_available` | `no buybox` |

If AOD ("See All Buying Options") opens and real offer cards are scraped, those rows still get their own `buybox_price` / `buybox_owner`. The main `price` column stays `price not available` for that ASIN.

## Code

- `_buybox_price_suppressed()`
- `_extract_main_price()` returns `price not available`
- `extract_buybox` sets `_price_suppressed` and skips broad price selectors
