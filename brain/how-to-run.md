# How to run (Windows + Mac)

## Setup (both OS)

1. Install Google Chrome
2. `pip install -r requirements.txt`
3. `python -m playwright install chromium`

## Walmart

**Windows:** double-click `start_chrome.bat`  
**Mac:**

```bash
chmod +x start_chrome.sh
./start_chrome.sh
```

Then pass captcha → `python scraper.py`

## Amazon

**Windows:** double-click `amazon/start_chrome.bat`  
**Mac:**

```bash
cd amazon
chmod +x start_chrome.sh
./start_chrome.sh
```

Then:

```bash
python scraper.py us
# or: uk / de / ca / ae / all
```

## Notes

- Scraper auto-finds Chrome on Windows and Mac if you just run `python scraper.py`
- Ports: Walmart `9222`, Amazon `9223`
- Quit all Chrome windows before starting the debug Chrome script
- Browser profile cookies stay local (not in git)
