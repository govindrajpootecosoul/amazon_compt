# Why "Not available" was wrong

Old Excel only had shipping/pickup/delivery stock labels from JSON (`NOT_AVAILABLE`), not the live UI cards.

Correct source is the product page tiles after ZIP is set:

- Shipping → Arrives today + Order within …
- Pickup → As soon as 7am today
- Delivery → As soon as 7am today

## Fix in script

1. Set ZIP cookies + UI before each product
2. Re-apply ZIP on the product page
3. Read fulfillment cards from DOM (`fulfillment.py`)
4. New output file: `output/walmart_data.xlsx`
5. `FORCE_RESCRAPE_ALL = True` in config.py (set False after one good full run)

## Run

1. Close Excel
2. `python scraper.py`
3. Open `output/walmart_data.xlsx`
