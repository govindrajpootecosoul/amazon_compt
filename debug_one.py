"""One-item diagnostic: what Walmart actually returns."""
import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

ITEM_ID = "12368855112"
ZIP_CODE = "35203"
DUMP = Path("output/debug_page.html")


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="chrome",
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-position=-32000,-32000",
                "--start-minimized",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1366, "height": 768},
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        url = f"https://www.walmart.com/ip/{ITEM_ID}"
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(12000)
        html = await page.content()
        title = await page.title()
        DUMP.parent.mkdir(parents=True, exist_ok=True)
        DUMP.write_text(html, encoding="utf-8")

        print("status:", resp.status if resp else None)
        print("title:", title)
        print("html_len:", len(html))
        print("has __NEXT_DATA__:", '__NEXT_DATA__' in html)
        print("has ld+json:", "application/ld+json" in html)
        print("has perimeterx string:", "perimeterx" in html.lower())
        print("has px-captcha:", "px-captcha" in html.lower())
        print("has press & hold:", "press & hold" in html.lower())
        print("visible h1:", await page.locator("h1").first.inner_text() if await page.locator("h1").count() else None)

        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        if m:
            payload = json.loads(m.group(1))
            product = (
                payload.get("props", {})
                .get("pageProps", {})
                .get("initialData", {})
                .get("data", {})
                .get("product")
            )
            print("product keys:", list(product.keys())[:40] if isinstance(product, dict) else type(product))
            if isinstance(product, dict):
                print("name:", product.get("name"))
                print("priceInfo:", product.get("priceInfo"))
                print("availability:", product.get("availabilityStatus"))
                print("brand:", product.get("brand"))
                print("shortDescription:", str(product.get("shortDescription"))[:200])
        else:
            print("NO __NEXT_DATA__")
            print("html snippet:", html[:800])

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
