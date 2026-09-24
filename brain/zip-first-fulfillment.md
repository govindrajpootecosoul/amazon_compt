# ZIP-first scraping + delivery timing

## Flow

1. Warmup Chrome / pass captcha
2. For each ZIP: set location UI/cookies FIRST
3. Then scrape each item for that ZIP

## Excel fulfillment columns (screenshot match)

| Column | Example from Walmart UI |
|--------|-------------------------|
| shipping | Available |
| shipping_eta | Arrives today |
| shipping_order_within | Order within 10 hr 49 min |
| pickup | Available |
| pickup_eta | As soon as 7am today |
| delivery | Available |
| delivery_eta | As soon as 7am today |

Out of stock items: status = Out of stock / Not available, ETA often empty.
