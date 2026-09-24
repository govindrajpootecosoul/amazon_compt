# Shipper / Seller junk in buybox_seller

## Cause

Amazon.ae AOD UI column header text `Shipper / Seller` was scraped as the seller name.

## Fix

- Reject labels: `Shipper / Seller`, `Sold by`, etc.
- Read the **next cell / sibling / seller link** for the real merchant name
- Skip header-only rows without a price

Re-run: `python scraper.py ae`
