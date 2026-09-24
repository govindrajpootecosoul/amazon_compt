@echo off
REM Opens Chrome with debug port 9223 for the Amazon scraper.
REM Tip: Close ALL other Chrome windows first if this fails.
REM (Walmart scraper uses 9222 — Amazon uses 9223)

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

echo Starting Chrome on port 9223 for Amazon...
start "" "%CHROME%" --remote-debugging-port=9223 --user-data-dir="%PROFILE%" https://www.amazon.com/

echo.
echo 1) Complete captcha on Amazon if shown
echo 2) Wait for Amazon homepage
echo 3) Run:  python scraper.py us
echo    Or:   python scraper.py uk / de / ca
echo.
pause
