#!/usr/bin/env bash
# Opens Chrome with debug port 9223 for the Amazon scraper.
# Tip: Quit ALL Chrome windows first if this fails.
# (Walmart scraper uses 9222 — Amazon uses 9223)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROFILE="$SCRIPT_DIR/browser_profile"

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [[ ! -x "$CHROME" ]]; then
  CHROME="$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
fi
if [[ ! -x "$CHROME" ]]; then
  CHROME="$(command -v google-chrome || true)"
fi
if [[ -z "${CHROME:-}" || ! -x "$CHROME" ]]; then
  echo "Google Chrome not found. Install Chrome first."
  exit 1
fi

echo
echo "IMPORTANT: Quit all Chrome windows first, then press Enter..."
read -r

echo "Starting Chrome on port 9223 for Amazon..."
"$CHROME" \
  --remote-debugging-port=9223 \
  --user-data-dir="$PROFILE" \
  "https://www.amazon.com/" &

echo
echo "1) Complete captcha on Amazon if shown"
echo "2) Wait for Amazon homepage"
echo "3) Run:  python scraper.py us"
echo "   Or:   python scraper.py uk / de / ca / ae"
echo
