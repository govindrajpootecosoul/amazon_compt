# Mac troubleshooting

## Error: `setDownloadBehavior` / `Browser context management is not supported`

Playwright tried to change Chrome download settings over CDP; newer Mac Chrome blocks that.

**Fix:**
```bash
pip3 install -U 'playwright>=1.60'
```
Code now connects with `no_defaults=True` so this should not happen after pull.

## Message: `Opening in existing browser session`

Chrome was already running, so Mac ignored `--remote-debugging-port` and reused the normal Chrome. Scraper then cannot control it.

**Fix:** `start_chrome.sh` now quits all Chrome first. Always:
1. Cmd+Q Chrome (fully quit)
2. `./start_chrome.sh`
3. Wait for Amazon homepage
4. `python3 scraper.py us`

## Screen lock / Chrome “closed”

Screen lock alone does **not** quit Chrome. What usually happened:
- Chrome never started in debug mode (see “existing browser session”), or
- Someone quit Chrome / Force Quit, or
- Terminal-launched Chrome died after a bad launch

Keep the Chrome window open while scraping. Lock is fine; Ctrl+C / Quit Chrome is not.

## Correct Mac order every run

```bash
cd amazon
./start_chrome.sh          # auto-quits old Chrome
# pass captcha if needed — leave Chrome open
python3 scraper.py us
```

Do **not** `cd amazon` twice — if prompt already shows `...amazon$`, you are inside `amazon/`.
