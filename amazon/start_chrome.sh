#!/usr/bin/env bash
# Opens Chrome with debug port 9223 for the Amazon scraper (Mac/Linux).
# CRITICAL on Mac: quit ALL Chrome first, or you get
#   "Opening in existing browser session" and debug port will NOT work.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROFILE="$SCRIPT_DIR/browser_profile"
PORT=9223

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
echo "Quitting ALL Google Chrome windows (required for debug port)..."
if [[ "$(uname -s)" == "Darwin" ]]; then
  osascript -e 'tell application "Google Chrome" to quit' 2>/dev/null || true
fi
killall "Google Chrome" 2>/dev/null || true
killall "Google Chrome Helper" 2>/dev/null || true
killall chrome 2>/dev/null || true
sleep 2

# Stale locks cause "Opening in existing browser session"
mkdir -p "$PROFILE"
rm -f "$PROFILE/SingletonLock" "$PROFILE/SingletonSocket" "$PROFILE/SingletonCookie" 2>/dev/null || true

echo "Starting Chrome on port $PORT for Amazon..."
echo "Profile: $PROFILE"

if [[ "$(uname -s)" == "Darwin" ]]; then
  # open -na = new instance as a real Mac app (survives Terminal better)
  open -na "Google Chrome" --args \
    --remote-debugging-port="$PORT" \
    --user-data-dir="$PROFILE" \
    --no-first-run \
    --no-default-browser-check \
    --disable-session-crashed-bubble \
    "https://www.amazon.com/"
else
  "$CHROME" \
    --remote-debugging-port="$PORT" \
    --user-data-dir="$PROFILE" \
    --no-first-run \
    --no-default-browser-check \
    "https://www.amazon.com/" &
fi

echo
echo "Wait ~5 seconds, then check Amazon homepage."
echo "1) Complete captcha if shown"
echo "2) Keep this Chrome window open (do not quit Chrome)"
echo "3) Mac screen lock is OK — do NOT Quit Chrome / Force Quit"
echo "4) Run:  python3 scraper.py us"
echo
