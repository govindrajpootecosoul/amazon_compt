# Buybox seller empty fix (esp. amazon.ae)

## Problem

`buybox_seller` was blank; `buybox_ships_from` / `buybox_fulfilled_by` often only showed `Delivered by`.

Amazon.ae uses different buy-box DOM (`offer-display-feature`, merchant info text) than .com.

## Fix

- Stronger seller selectors + "Sold by X" / "Delivered by X" parsing
- Clean junk labels so bare `Delivered by` is dropped
- Fallback to `Amazon.ae` when page clearly ships/sold by Amazon.ae
- Same logic applies to US/UK/DE/CA AOD offers

Re-run: `python scraper.py ae` (new dated Excel folder).
