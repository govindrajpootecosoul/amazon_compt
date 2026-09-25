# Captcha / "Please try again"

## Why Press & Hold fails

The gray bar **"unsupported command-line flag: --no-sandbox"** means Playwright opened Chrome. PerimeterX detects automation, so even holding the button shows **"Please try again"**.

## Fix (3 steps)

1. **Close** the Playwright Chrome window from `python scraper.py`.
2. Start normal Chrome with debug port:
   - **Windows:** double-click `start_chrome.bat`
   - **Mac:** `chmod +x start_chrome.sh && ./start_chrome.sh`
   - Opens normal Chrome (no automation flags) on port 9222.
3. In **that** Chrome:
   - Go to https://www.walmart.com/
   - Complete **Press & Hold** until the real homepage loads.
4. Run:
   ```bash
   python scraper.py
   ```
5. When terminal asks, press **ENTER** after Walmart homepage is open.

The scraper connects to your Chrome (`CONNECT_EXISTING_CHROME = True` in `config.py`) and then fills Excel with title, price, description, etc.

## If still blocked

- Wait 30–60 minutes (IP may be temporarily flagged).
- Set `PROXY_SERVER` in `config.py` to a US residential proxy.
- Delete `browser_profile/` and start fresh with `start_chrome.bat` / `start_chrome.sh`.
