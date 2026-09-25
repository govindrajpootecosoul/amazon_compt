# AOD "Sold by" owner (not "More")

## Rule

**Owner = text right after `Sold by`** (same row / soldBy block / regex).

Never use position-based "More" / "Details" / expand links.

## Examples

- `Sold by Kolhapurwala's` → `Kolhapurwala's`
- `Sold by PEAK NEST FZ LLE` → `PEAK NEST FZ LLE`
- `TGMarket AE TGMarket AE Sold by TGMarket AE` → `TGMarket AE` (deduped)

## Parallel + blank winners

When main buy box has price but blank owner, fill from:

1. Same-price AOD offer (`_norm_price_key`)
2. Pinned / first AOD card
3. Ships-from / fulfilled Amazon label only (not blind Amazon.ae)
