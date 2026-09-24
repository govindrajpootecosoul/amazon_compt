# Scraped fields

Excel columns (order):

item_id, zip_code, scraped_at, title, brand, price, was_price, currency, availability,
shipping, shipping_eta, shipping_order_within,
pickup, pickup_eta,
delivery, delivery_eta,
rating, reviews_count, model, upc, gtin, category, description, image_url, seller,
url, http_status, status, error

## Fulfillment (matches Walmart "How you'll get this item")

| Column | Screenshot example |
|--------|--------------------|
| shipping | Available |
| shipping_eta | Arrives today |
| shipping_order_within | Order within 10 hr 49 min |
| pickup | Available |
| pickup_eta | As soon as 7am today |
| delivery | Available |
| delivery_eta | As soon as 7am today |

ZIP must be set first so these times are for that location.
