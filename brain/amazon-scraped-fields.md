# Amazon scraped fields

Excel columns (order):

asin, marketplace, scraped_at, title, brand, price, was_price, currency,
availability,
offer_index, offer_count, is_buybox_winner,
buybox_available, buybox_price, buybox_owner, buybox_ships_from,
buybox_fulfilled_by, buybox_is_amazon, buybox_condition, buybox_delivery,
rating, reviews_count, category,

## Product information (from Amazon tables)

Item details: recommended_uses, number_of_pieces, included_components,
item_type_name, reusability, gtin, manufacturer, upc, item_highlight,
unit_count, model_number, manufacturer_part_number, best_sellers_rank

Style: color, style_name, shape, pattern, finish_types, theme,
occasion_type, seasons

Materials & Care: material_type, product_care_instructions, material_features

Measurements: item_dimensions, size, item_weight, net_content_weight

Features & Specs: special_features

Catch-all: product_details_raw (all label:value pairs found on page)

Then: features, description, image_url, seller, ships_from,
url, http_status, status, error

## Buy box / offers

| Column | Meaning |
|--------|---------|
| offer_index | 1, 2, 3… (one row per seller offer) |
| offer_count | How many offers found for this ASIN |
| is_buybox_winner | Yes for primary buy box |
| buybox_available | `Yes` / `no buybox` |
| buybox_owner | **Sold by** merchant name (not "Details" / "See less") |
| price | Numeric buy-box price, or **`price not available`** when Amazon shows "Price higher than typical" |

If product has no buy box → one row with `buybox_available = no buybox`.
When price is suppressed, do not copy carousel / "options from AED …" into `price`.

## Output path

`amazon/output/{YYYY-MM-DD_HHMMSS}/amazon_{geo}_data.xlsx` — new file every run.
