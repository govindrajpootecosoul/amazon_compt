@echo off
REM Opens Chrome with debug port 9222 for the Walmart scraper.
REM Tip: Close ALL other Chrome windows first if this fails.
REM (Amazon scraper uses 9223 — Walmart uses 9222)

set "PROFILE=%~dp0browser_profile"
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

if not exist "%CHROME%" (
    echo Google Chrome not found. Install Chrome first.
    pause
    exit /b 1
)

echo.
echo IMPORTANT: Close all Chrome windows first, then press any key...
pause >nul

echo Starting Chrome on port 9222 for Walmart...
start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%PROFILE%" https://www.walmart.com/

echo.
echo 1) Complete Press and Hold captcha if shown
echo 2) Wait for Walmart homepage
echo 3) Run:  python scraper.py
echo.
pause
