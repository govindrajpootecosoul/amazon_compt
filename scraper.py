"""
Walmart product scraper.

Paste Item IDs into inputs/item_ids.txt and ZIP codes into inputs/zip_codes.txt,
then run:  python scraper.py

Output: output/walmart_scraped_data.xlsx
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

import config
import fulfillment

COLUMNS = [
    "item_id",
    "zip_code",
    "scraped_at",
    "title",
    "brand",
    "price",
    "was_price",
    "currency",
    "availability",
    "shipping",
    "shipping_eta",
    "shipping_order_within",
    "pickup",
    "pickup_eta",
    "delivery",
    "delivery_eta",
    "rating",
    "reviews_count",
    "model",
    "upc",
    "gtin",
    "category",
    "description",
    "image_url",
    "seller",
    "url",
    "http_status",
    "status",
    "error",
]


# ---------------------------------------------------------------------------
# Stealth
# ---------------------------------------------------------------------------


async def apply_stealth(page: Page) -> None:
    try:
        from playwright_stealth import Stealth

        await Stealth().apply_stealth_async(page)
        return
    except Exception:
        pass
    try:
        from playwright_stealth import stealth_async

        await stealth_async(page)
        return
    except Exception:
        pass
    await page.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """
    )


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def _read_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    values: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for part in re.split(r"[\s,;|]+", line):
            part = part.strip().strip('"').strip("'")
            if part and part.lower() not in {"item_id", "zip_code", "zip", "id"}:
                values.append(part)
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def load_list(path_str: str, column_candidates: list[str]) -> list[str]:
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path.resolve()}")
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str)
        col = next((c for c in column_candidates if c in df.columns), df.columns[0])
        values = df[col].dropna().astype(str).str.strip().tolist()
        seen: set[str] = set()
        out: list[str] = []
        for v in values:
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out
    return _read_lines(path)


def job_key(item_id: str, zip_code: str) -> str:
    return f"{item_id}::{zip_code}"


def load_results(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        if path.suffix.lower() in {".xlsx", ".xls"}:
            existing = pd.read_excel(path, dtype=str)
        else:
            existing = pd.read_csv(path, dtype=str)
        return existing.fillna("").to_dict(orient="records")
    except Exception:
        return []


def save_results(results: list[dict[str, Any]], path: Path) -> Path:
    """
    Save Excel/CSV. If the file is open in Excel (Permission denied),
    retry then write to an alternate autosave file so scraping continues.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(results)
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[COLUMNS]

    def _write(target: Path) -> None:
        if target.suffix.lower() in {".xlsx", ".xls"}:
            df.to_excel(target, index=False, engine="openpyxl")
        else:
            df.to_csv(target, index=False)

    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            _write(path)
            return path
        except PermissionError as e:
            last_err = e
            print(
                f"[SAVE] Permission denied writing {path.name} "
                f"(file is probably open in Excel). Retry {attempt}/3..."
            )
            time.sleep(1.5)
        except Exception as e:
            last_err = e
            break

    # Fallback: don't crash the whole scrape
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    alt = path.with_name(f"{path.stem}_autosave_{stamp}{path.suffix}")
    try:
        _write(alt)
        print(f"[SAVE] Could not overwrite {path.name} (close Excel!).")
        print(f"[SAVE] Data saved instead to: {alt.resolve()}")
        return alt
    except Exception as e:
        print(f"[SAVE] FATAL save error: {last_err or e}")
        raise


def upsert_result(results: list[dict[str, Any]], row: dict[str, Any]) -> list[dict[str, Any]]:
    key = job_key(str(row.get("item_id", "")), str(row.get("zip_code", "")))
    out = [
        r
        for r in results
        if job_key(str(r.get("item_id", "")), str(r.get("zip_code", ""))) != key
    ]
    out.append(row)
    return out


def successful_keys(results: list[dict[str, Any]]) -> set[str]:
    if getattr(config, "FORCE_RESCRAPE_ALL", False):
        return set()
    keys: set[str] = set()
    for r in results:
        if str(r.get("status", "")).lower() != "success" or not r.get("title"):
            continue
        if not fulfillment.fulfillment_looks_complete(r):
            continue
        keys.add(job_key(str(r.get("item_id", "")), str(r.get("zip_code", ""))))
    return keys


# ---------------------------------------------------------------------------
# Challenge / block detection (real challenge page only)
# ---------------------------------------------------------------------------


def is_challenge_html(html: str, page_title: str = "") -> bool:
    title = (page_title or "").lower()
    if "robot or human" in title:
        return True
    lower = html.lower()
    if "px-captcha" in lower:
        return True
    if "<title>robot or human?</title>" in lower:
        return True
    return False


async def wait_out_challenge(page: Page, seconds: int) -> bool:
    """Return True if the page is no longer a challenge."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        html = await page.content()
        title = await page.title()
        if not is_challenge_html(html, title):
            return True
        await asyncio.sleep(2.0)
    return False


async def manual_warmup(page: Page) -> bool:
    """Let user pass captcha in a real Chrome session before scraping."""
    if not config.MANUAL_WARMUP:
        return True

    print("\n" + "=" * 62)
    if config.CONNECT_EXISTING_CHROME:
        hint = chrome_start_script()
        print(f"WARMUP: Use Chrome opened by {hint} (NOT Playwright Chrome).")
        if sys.platform == "win32":
            print(f"  1) Double-click {hint}")
        else:
            print(f"  1) In Terminal: chmod +x {hint} && ./{hint}")
        print("  2) In that Chrome, open https://www.walmart.com/")
        print("  3) Complete 'Press & Hold' until homepage loads")
        print("  4) Come back here and press ENTER")
    else:
        print("WARMUP: Complete Walmart captcha in the browser window, then press ENTER.")
    print("=" * 62)

    try:
        await page.goto("https://www.walmart.com/", wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"[WARMUP] Could not open Walmart: {e}")

    await asyncio.to_thread(input, "\nPress ENTER when Walmart homepage is open (no captcha)... ")

    html = await page.content()
    title = await page.title()
    if is_challenge_html(html, title):
        print("[WARMUP] Still on 'Robot or human?'. Captcha did NOT pass.")
        print(f"[WARMUP] Close Playwright Chrome. Use {chrome_start_script()} instead, then run again.")
        return False

    print("[WARMUP] OK — Walmart session looks good. Starting scrape...\n")
    return True


# ---------------------------------------------------------------------------
# ZIP / location
# ---------------------------------------------------------------------------


def make_location_cookies(zip_code: str, store_id: str | None = None) -> list[dict[str, Any]]:
    ts = int(time.time() * 1000)
    acid = str(uuid.uuid4())
    loc: dict[str, Any] = {
        "intent": "SHIPPING",
        "storeIntent": "PICKUP",
        "mergeFlag": True,
        "postalCode": {"base": str(zip_code), "timestamp": ts},
        "validateKey": f"prod:v2:{acid}",
    }
    if store_id:
        loc["pickup"] = {"nodeId": str(store_id), "timestamp": ts}
        loc["shippingStore"] = {"nodeId": str(store_id), "timestamp": ts}
    encoded = quote(json.dumps(loc, separators=(",", ":")))
    common = {"domain": ".walmart.com", "path": "/"}
    cookies = [
        {**common, "name": "ACID", "value": acid},
        {**common, "name": "hasACID", "value": "true"},
        {**common, "name": "hasLocData", "value": "1"},
        {**common, "name": "locGuestData", "value": encoded},
        {**common, "name": "locDataV3", "value": encoded},
    ]
    if store_id:
        cookies.append({**common, "name": "assortmentStoreId", "value": str(store_id)})
    return cookies


async def _close_popups(page: Page) -> None:
    for sel in [
        'button[aria-label="Close"]',
        'button:has-text("Close")',
        'button:has-text("Accept all")',
        'button:has-text("I agree")',
        'button:has-text("Not now")',
        'button:has-text("No thanks")',
        '[data-automation-id="overlay-backdrop-close"]',
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.count() and await btn.is_visible():
                await btn.click(timeout=1500)
                await asyncio.sleep(0.3)
        except Exception:
            pass


async def set_zip_via_ui(page: Page, zip_code: str) -> bool:
    """Open Walmart location UI and set ZIP so delivery dates match that area."""
    await _close_popups(page)

    triggers = [
        '[data-automation-id="fulfillment-banner-location"]',
        'button[data-automation-id="fulfillment-address"]',
        'button[aria-label*="Deliver to" i]',
        'button[aria-label*="Pickup" i]',
        'button:has-text("Pickup or delivery")',
        'button:has-text("How do you want your items")',
        'button:has-text("Deliver to")',
        'button:has-text("Add address")',
        '#location-button',
    ]
    opened = False
    for sel in triggers:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=3000)
                opened = True
                await asyncio.sleep(1.0)
                break
        except Exception:
            continue

    if not opened:
        return False

    # Prefer Shipping / Delivery tab when choosing ZIP
    for label in ("Shipping", "Delivery", "Deliver", "Use my current location"):
        try:
            tab = page.get_by_role("button", name=re.compile(label, re.I)).first
            if await tab.count() and await tab.is_visible():
                if "current location" in label.lower():
                    continue
                await tab.click(timeout=2000)
                await asyncio.sleep(0.4)
        except Exception:
            pass

    zip_inputs = [
        'input[data-automation-id="zipcode-input"]',
        'input[name="zipCode"]',
        'input[name="zipcode"]',
        'input[aria-label*="zip" i]',
        'input[placeholder*="ZIP" i]',
        'input[placeholder*="zip" i]',
        'input[type="tel"]',
        'input[inputmode="numeric"]',
    ]
    filled = False
    for sel in zip_inputs:
        try:
            inp = page.locator(sel).first
            if await inp.count() and await inp.is_visible():
                await inp.click(timeout=2000)
                await inp.fill("")
                await inp.type(str(zip_code), delay=random.randint(50, 100))
                filled = True
                break
        except Exception:
            continue

    if not filled:
        return False

    for submit_sel in [
        'button[data-automation-id="save-label"]',
        'button:has-text("Save")',
        'button:has-text("Continue")',
        'button:has-text("Update")',
        'button:has-text("Apply")',
        'button[type="submit"]',
    ]:
        try:
            btn = page.locator(submit_sel).first
            if await btn.count() and await btn.is_visible():
                await btn.click(timeout=3000)
                break
        except Exception:
            continue

    await asyncio.sleep(1.5)

    # Store picker may appear — choose first store for pickup context
    for store_sel in [
        'button:has-text("Make my store")',
        'button:has-text("Set as my store")',
        'button:has-text("Select")',
        '[data-automation-id="store-tile"] button',
        '[data-automation-id="store-tile"]',
    ]:
        try:
            store = page.locator(store_sel).first
            if await store.count() and await store.is_visible():
                await store.click(timeout=2500)
                await asyncio.sleep(1.0)
                break
        except Exception:
            continue

    await _close_popups(page)
    return True


async def set_walmart_location(context: BrowserContext, page: Page, zip_code: str) -> bool:
    """
    1) Set location cookies for ZIP
    2) Open homepage
    3) Set ZIP in Walmart UI (so Shipping/Pickup/Delivery ETAs match ZIP)
    """
    print(f"[LOCATION] Setting ZIP {zip_code} BEFORE scraping products...")
    try:
        await context.add_cookies(make_location_cookies(zip_code))
        await page.goto("https://www.walmart.com/", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(random.uniform(1.5, 2.5))

        html = await page.content()
        title = await page.title()
        if is_challenge_html(html, title):
            print("[LOCATION] Challenge page — complete captcha in Chrome, waiting...")
            ok = await wait_out_challenge(page, config.CAPTCHA_WAIT_SECONDS)
            if not ok:
                print("[LOCATION] Still blocked.")
                return False

        # API location update (best-effort)
        try:
            api_result = await page.evaluate(
                """async (zip) => {
                    const out = { ok: false, storeId: null, body: null };
                    try {
                        const res = await fetch('/orchestra/home/graphql', { method: 'GET', credentials: 'include' });
                    } catch (e) {}
                    try {
                        const res = await fetch('/account/api/location', {
                            method: 'PUT',
                            credentials: 'include',
                            headers: {'content-type': 'application/json', 'accept': 'application/json'},
                            body: JSON.stringify({
                                postalCode: String(zip),
                                persistLocation: true,
                                clientName: 'Web-Header-Location'
                            })
                        });
                        out.ok = res.ok;
                        try { out.body = await res.json(); } catch (e) { out.body = await res.text(); }
                        const store =
                            (out.body && (out.body.storeId || out.body.assortmentStoreId
                              || (out.body.data && out.body.data.storeId))) || null;
                        out.storeId = store;
                    } catch (e) {
                        out.error = String(e);
                    }
                    return out;
                }""",
                zip_code,
            )
            store_id = None
            if isinstance(api_result, dict):
                store_id = api_result.get("storeId")
                if store_id:
                    await context.add_cookies(make_location_cookies(zip_code, str(store_id)))
                    print(f"[LOCATION] API storeId={store_id} for ZIP {zip_code}")
        except Exception:
            pass

        ui_ok = await set_zip_via_ui(page, zip_code)
        if ui_ok:
            print(f"[LOCATION] ZIP {zip_code} set via UI. Now scraping products for this ZIP...")
        else:
            print(f"[LOCATION] UI ZIP input not found — cookies/API used for {zip_code}. Continuing...")

        # Confirm ZIP visible somewhere on page header if possible
        try:
            body = await page.inner_text("body")
            if zip_code in body or zip_code[:3] in body:
                print(f"[LOCATION] Confirmed ZIP text appears on page.")
        except Exception:
            pass

        await asyncio.sleep(1.0)
        return True
    except Exception as e:
        print(f"[LOCATION] Error for ZIP {zip_code}: {e}")
        return False


async def ensure_zip_on_product_page(page: Page, zip_code: str) -> None:
    """On the product page, open location picker and set ZIP so fulfillment ETAs refresh."""
    try:
        await set_zip_via_ui(page, zip_code)
        await asyncio.sleep(1.2)
    except Exception:
        pass


def _page_item_id_from_html(html: str) -> str | None:
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        payload = json.loads(m.group(1))
    except Exception:
        return None
    product = _find_product_node(payload)
    if not product:
        return None
    for key in ("usItemId", "id", "offerId", "itemId"):
        val = product.get(key)
        if val is not None:
            return str(val)
    return None


# ---------------------------------------------------------------------------
# Product JSON extraction
# ---------------------------------------------------------------------------


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        for k in ("name", "displayName", "value", "price", "priceString"):
            if value.get(k) is not None:
                return _as_text(value.get(k))
    return None


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:2000] or None


def _find_product_node(obj: Any, depth: int = 0) -> dict[str, Any] | None:
    if depth > 12:
        return None
    if isinstance(obj, dict):
        has_name = isinstance(obj.get("name"), str) and len(obj.get("name") or "") > 3
        looks_product = has_name and (
            "priceInfo" in obj
            or "usItemId" in obj
            or "availabilityStatus" in obj
            or "idml" in obj
        )
        if looks_product:
            return obj
        for key in (
            "product",
            "productData",
            "data",
            "initialData",
            "pageProps",
            "props",
        ):
            if key in obj:
                found = _find_product_node(obj[key], depth + 1)
                if found:
                    return found
        for v in obj.values():
            found = _find_product_node(v, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj[:60]:
            found = _find_product_node(item, depth + 1)
            if found:
                return found
    return None


def _parse_next_data(html: str) -> dict[str, Any]:
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return {}
    try:
        payload = json.loads(m.group(1))
    except Exception:
        return {}
    product = _find_product_node(payload)
    if not product:
        return {}
    return _fields_from_product(product)


def _parse_json_ld(html: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.I,
    ):
        raw = m.group(1).strip()
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            types = item.get("@type")
            type_str = " ".join(types) if isinstance(types, list) else str(types or "")
            if "product" not in type_str.lower():
                continue
            out["title"] = out.get("title") or _as_text(item.get("name"))
            brand = item.get("brand")
            out["brand"] = out.get("brand") or _as_text(brand)
            desc = item.get("description")
            out["description"] = out.get("description") or _strip_html(_as_text(desc))
            image = item.get("image")
            if isinstance(image, list) and image:
                out["image_url"] = out.get("image_url") or _as_text(image[0])
            else:
                out["image_url"] = out.get("image_url") or _as_text(image)
            offers = item.get("offers")
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                out["price"] = out.get("price") or _as_text(offers.get("price"))
                out["currency"] = out.get("currency") or _as_text(offers.get("priceCurrency")) or "USD"
                avail = _as_text(offers.get("availability")) or ""
                if "outofstock" in avail.lower().replace("_", "").replace(" ", ""):
                    out["availability"] = "Out of Stock"
                elif "instock" in avail.lower().replace("_", "").replace(" ", ""):
                    out["availability"] = "In Stock"
                seller = offers.get("seller")
                out["seller"] = out.get("seller") or _as_text(seller)
            rating = item.get("aggregateRating")
            if isinstance(rating, dict):
                out["rating"] = out.get("rating") or _as_text(rating.get("ratingValue"))
                out["reviews_count"] = out.get("reviews_count") or _as_text(
                    rating.get("reviewCount") or rating.get("ratingCount")
                )
            gtin = item.get("gtin13") or item.get("gtin") or item.get("sku")
            out["gtin"] = out.get("gtin") or _as_text(gtin)
            out["model"] = out.get("model") or _as_text(item.get("mpn") or item.get("model"))
    return out


def _fields_from_product(product: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["title"] = _as_text(product.get("name") or product.get("productName"))
    out["brand"] = _as_text(product.get("brand"))
    price_info = product.get("priceInfo") or {}
    if isinstance(price_info, dict):
        current = price_info.get("currentPrice") or {}
        was = price_info.get("wasPrice") or {}
        if isinstance(current, dict):
            price = current.get("priceString") or current.get("price")
            out["price"] = _as_text(price)
            if isinstance(price, (int, float)):
                out["price"] = f"${price}"
            out["currency"] = _as_text(current.get("currencyUnit")) or "USD"
        if isinstance(was, dict):
            was_p = was.get("priceString") or was.get("price")
            out["was_price"] = _as_text(was_p)
            if isinstance(was_p, (int, float)):
                out["was_price"] = f"${was_p}"
        if not out.get("price"):
            out["price"] = _as_text(price_info.get("priceRangeString") or price_info.get("currentPrice"))

    avail = product.get("availabilityStatus") or product.get("availabilityStatusV2")
    out["availability"] = _as_text(avail)

    rating = product.get("averageRating")
    if rating is None and isinstance(product.get("rating"), dict):
        rating = product["rating"].get("averageRating")
    out["rating"] = _as_text(rating)

    reviews = product.get("numberOfReviews")
    if reviews is None and isinstance(product.get("rating"), dict):
        reviews = product["rating"].get("numberOfReviews")
    out["reviews_count"] = _as_text(reviews)

    out["model"] = _as_text(product.get("model") or product.get("modelNumber"))
    out["upc"] = _as_text(product.get("upc") or product.get("upcCode"))
    out["gtin"] = _as_text(product.get("gtin") or product.get("gtin13"))

    category = product.get("category") or product.get("categoryPath")
    if isinstance(category, dict):
        path = category.get("path") or category.get("categoryPath")
        if isinstance(path, list):
            names = [_as_text(p.get("name") if isinstance(p, dict) else p) for p in path]
            out["category"] = " > ".join([n for n in names if n])
        else:
            out["category"] = _as_text(category.get("name"))
    else:
        out["category"] = _as_text(category)

    desc = product.get("shortDescription") or product.get("longDescription")
    idml = product.get("idml") or {}
    if not desc and isinstance(idml, dict):
        desc = idml.get("shortDescription") or idml.get("longDescription")
    out["description"] = _strip_html(_as_text(desc))

    image_info = product.get("imageInfo") or {}
    if isinstance(image_info, dict):
        out["image_url"] = _as_text(image_info.get("thumbnailUrl"))
        if not out["image_url"]:
            images = image_info.get("allImages") or image_info.get("images") or []
            if isinstance(images, list) and images:
                first = images[0]
                out["image_url"] = _as_text(first.get("url") if isinstance(first, dict) else first)
    if not out.get("image_url"):
        out["image_url"] = _as_text(product.get("imageUrl") or product.get("image"))

    out["seller"] = _as_text(
        product.get("sellerName")
        or product.get("sellerDisplayName")
        or product.get("seller")
    )

    fulfillment = _extract_fulfillment_statuses(product)
    out.update(fulfillment)
    return {k: v for k, v in out.items() if v}


def _humanize_status(raw: str | None) -> str | None:
    if not raw:
        return None
    text = re.sub(r"\s+", " ", str(raw)).strip()
    key = text.upper().replace("-", "_").replace(" ", "_")
    mapping = {
        "OUT_OF_STOCK": "Out of stock",
        "OUTOFSTOCK": "Out of stock",
        "IN_STOCK": "In stock",
        "INSTOCK": "In stock",
        "AVAILABLE": "Available",
        "NOT_AVAILABLE": "Not available",
        "NOTAVAILABLE": "Not available",
        "UNAVAILABLE": "Not available",
        "CHECK_NEARBY": "Check nearby",
        "CHECKNEARBY": "Check nearby",
        "LIMITED": "Limited stock",
    }
    if key in mapping:
        return mapping[key]
    # Already human text from UI
    lower = text.lower()
    if "out of stock" in lower:
        return "Out of stock"
    if "check nearby" in lower:
        return "Check nearby"
    if "not available" in lower:
        return "Not available"
    if "in stock" in lower or "available" in lower:
        return text
    return text


def _option_channel(opt: dict[str, Any]) -> str | None:
    """Classify fulfillment option as shipping / pickup / delivery."""
    parts = [
        _as_text(opt.get("type")),
        _as_text(opt.get("fulfillment")),
        _as_text(opt.get("fulfillmentMethod")),
        _as_text(opt.get("fulfillmentType")),
        _as_text(opt.get("name")),
        _as_text(opt.get("label")),
        _as_text(opt.get("id")),
    ]
    blob = " ".join([p for p in parts if p]).lower()
    blob += " " + json.dumps(opt).lower()
    if "pickup" in blob or "store" in blob or "curbside" in blob:
        return "pickup"
    if "delivery" in blob or "scheduled" in blob or "express" in blob:
        # Prefer delivery when clearly delivery (not shipping)
        if "shipping" in blob and "delivery" not in " ".join([p for p in parts if p]).lower():
            return "shipping"
        if re.search(r"\bdelivery\b", blob):
            return "delivery"
    if "shipping" in blob or "ship" in blob:
        return "shipping"
    if "delivery" in blob:
        return "delivery"
    return None


def _looks_like_eta(text: str) -> bool:
    lower = text.lower()
    if lower in {"out of stock", "in stock", "not available", "available", "check nearby"}:
        return False
    if "order within" in lower:
        return False  # cutoff countdown, not ETA
    if "stock" in lower and "arriv" not in lower and "by " not in lower:
        return False
    if any(
        x in lower
        for x in (
            "arriv",
            "by ",
            "today",
            "tomorrow",
            "get it",
            "as soon",
            "am",
            "pm",
            "mon",
            "tue",
            "wed",
            "thu",
            "fri",
            "sat",
            "sun",
        )
    ):
        return True
    return bool(re.search(r"\b\d{1,2}/\d{1,2}\b", text))


def _looks_like_order_within(text: str) -> bool:
    return "order within" in (text or "").lower()


def _option_status(opt: dict[str, Any]) -> str | None:
    for key in (
        "availabilityStatus",
        "availableStatus",
        "status",
        "availability",
        "message",
        "secondaryLabel",
        "subtext",
    ):
        val = opt.get(key)
        if isinstance(val, dict):
            nested = (
                val.get("status")
                or val.get("availabilityStatus")
                or val.get("message")
                or val.get("text")
                or val.get("label")
            )
            text = _as_text(nested)
        else:
            text = _as_text(val)
        if text and not _looks_like_eta(text) and not _looks_like_order_within(text):
            return _humanize_status(text)

    for key in ("productLocation", "inventory", "storeAvailability"):
        nested = opt.get(key)
        if isinstance(nested, dict):
            text = _as_text(
                nested.get("availabilityStatus")
                or nested.get("status")
                or nested.get("message")
            )
            if text and not _looks_like_eta(text) and not _looks_like_order_within(text):
                return _humanize_status(text)
    return None


def _collect_text_candidates(opt: dict[str, Any]) -> list[str]:
    texts: list[str] = []

    def add(val: Any) -> None:
        text = _as_text(val)
        if text:
            texts.append(re.sub(r"\s+", " ", text).strip())

    for key in (
        "sla",
        "slaText",
        "speedDetails",
        "fulfillmentText",
        "deliveryDate",
        "earliestSlot",
        "maxDeliveryDate",
        "minDeliveryDate",
        "arrivalDate",
        "promisedDate",
        "secondaryMessage",
        "message",
        "description",
        "subtext",
        "secondaryLabel",
        "availableDate",
        "cutoffTime",
        "orderBy",
        "orderByText",
    ):
        val = opt.get(key)
        if isinstance(val, dict):
            for nk in (
                "slaText",
                "value",
                "text",
                "label",
                "message",
                "displayValue",
                "dateString",
                "shortDateString",
            ):
                add(val.get(nk))
            add(val)
        else:
            add(val)

    for key in ("speed", "slot", "fulfillmentDate", "deliverySlot"):
        nested = opt.get(key)
        if isinstance(nested, dict):
            for nk in ("slaText", "text", "label", "message", "displayValue", "dateString"):
                add(nested.get(nk))
    return list(dict.fromkeys(texts))


def _option_eta(opt: dict[str, Any]) -> str | None:
    """Primary timing: 'Arrives today' / 'As soon as 7am today'."""
    candidates = [t for t in _collect_text_candidates(opt) if _looks_like_eta(t)]
    if not candidates:
        return None
    candidates.sort(key=len, reverse=True)
    return candidates[0][:200]


def _option_order_within(opt: dict[str, Any]) -> str | None:
    """Shipping countdown: 'Order within 10 hr 49 min'."""
    for t in _collect_text_candidates(opt):
        if _looks_like_order_within(t):
            return t[:200]
    return None


def _extract_fulfillment_statuses(product: dict[str, Any]) -> dict[str, str]:
    """
    Map Walmart fulfillment tiles into:
      shipping / pickup / delivery
      shipping_eta / pickup_eta / delivery_eta
      shipping_order_within
    """
    result: dict[str, str] = {}
    sources: list[Any] = []
    for key in (
        "fulfillmentOptions",
        "fulfillmentSummary",
        "fulfillmentLabelGroup",
        "fulfillment",
        "shippingOption",
        "pickupOption",
        "deliveryOption",
    ):
        val = product.get(key)
        if val:
            sources.append(val)

    options: list[dict[str, Any]] = []

    def collect(obj: Any, depth: int = 0) -> None:
        if depth > 7:
            return
        if isinstance(obj, dict):
            if any(
                k in obj
                for k in (
                    "availabilityStatus",
                    "fulfillmentType",
                    "fulfillmentMethod",
                    "type",
                    "sla",
                    "slaText",
                )
            ) and (
                _option_channel(obj)
                or any(t in json.dumps(obj).lower() for t in ("shipping", "pickup", "delivery"))
            ):
                options.append(obj)
            for v in obj.values():
                collect(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj[:50]:
                collect(item, depth + 1)

    for src in sources:
        collect(src)

    for opt in options:
        channel = _option_channel(opt)
        if not channel:
            continue
        status = _option_status(opt)
        eta = _option_eta(opt)
        order_within = _option_order_within(opt) if channel == "shipping" else None

        if status and channel not in result:
            result[channel] = status
        elif eta and channel not in result:
            # When tile shows "Arrives today" with no OUT_OF_STOCK, treat as available
            result[channel] = "Available"

        if eta and f"{channel}_eta" not in result:
            result[f"{channel}_eta"] = eta
        if order_within and "shipping_order_within" not in result:
            result["shipping_order_within"] = order_within

    if "shipping" not in result:
        top = _humanize_status(
            _as_text(product.get("availabilityStatus") or product.get("availabilityStatusV2"))
        )
        if top:
            result["shipping"] = top

    return result


async def extract_fulfillment_from_dom(page: Page) -> dict[str, str]:
    """
    Parse 'How you'll get this item' cards exactly like the UI:
      Shipping -> Arrives today + Order within 10 hr 49 min
      Pickup   -> As soon as 7am today
      Delivery -> As soon as 7am today
    """
    try:
        data = await page.evaluate(
            """() => {
                const out = {};
                const normalize = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                const isStock = (t) => {
                    const l = (t || '').toLowerCase();
                    return ['out of stock','in stock','not available','available','check nearby'].includes(l);
                };
                const isOrderWithin = (t) => /order within/i.test(t || '');
                const isEta = (t) => {
                    const l = (t || '').toLowerCase();
                    if (!l || isStock(l) || isOrderWithin(l)) return false;
                    return /(arriv|as soon|today|tomorrow|by |get it|\\bam\\b|\\bpm\\b|\\d{1,2}\\/\\d{1,2})/i.test(l);
                };

                // Prefer the fulfillment section when present
                let roots = [];
                const section = Array.from(document.querySelectorAll('section, div, form')).find(el => {
                    const t = normalize(el.innerText || '');
                    return t.startsWith("How you'll get this item") || t.includes("How you'll get this item");
                });
                if (section) roots = [section];
                else roots = [document.body];

                const labels = ['Shipping', 'Pickup', 'Delivery'];
                for (const root of roots) {
                    const nodes = Array.from(root.querySelectorAll('button, div, li, label, span'));
                    for (const label of labels) {
                        const key = label.toLowerCase();
                        if (out[key] || out[key + '_eta']) continue;

                        for (const el of nodes) {
                            // Find a compact card whose first meaningful line is the label
                            let cur = el;
                            let cardText = '';
                            for (let up = 0; up < 5 && cur; up++) {
                                const t = normalize(cur.innerText || '');
                                if (t && t.length <= 220 && t.toLowerCase().includes(label.toLowerCase())) {
                                    cardText = t;
                                    // Prefer shorter matching containers (actual tile)
                                    if (t.length <= 120) break;
                                }
                                cur = cur.parentElement;
                            }
                            if (!cardText) continue;

                            const lines = (el.closest('button, [role="radio"], [data-testid], label, div')?.innerText || cardText)
                                .split('\\n').map(normalize).filter(Boolean);

                            // Find index of label line
                            let idx = lines.findIndex(l => l.toLowerCase() === label.toLowerCase());
                            if (idx < 0) {
                                // Single-line "Shipping Arrives today..."
                                if (!cardText.toLowerCase().startsWith(label.toLowerCase())) continue;
                                lines.length = 0;
                                lines.push(label);
                                lines.push(cardText.slice(label.length).trim());
                                idx = 0;
                            }

                            const after = lines.slice(idx + 1);
                            let status = '';
                            let eta = '';
                            let orderWithin = '';
                            for (const line of after) {
                                if (isOrderWithin(line) && !orderWithin) orderWithin = line;
                                else if (isEta(line) && !eta) eta = line;
                                else if (isStock(line) && !status) status = line;
                                else if (!status && !isEta(line) && !isOrderWithin(line) && line.toLowerCase() !== label.toLowerCase()) {
                                    // e.g. leftover text
                                    if (isEta(line)) eta = line;
                                    else status = line;
                                }
                            }

                            if (!eta && !status && !orderWithin) continue;

                            if (status) out[key] = status;
                            else if (eta) out[key] = 'Available';
                            if (eta) out[key + '_eta'] = eta;
                            if (orderWithin && key === 'shipping') out['shipping_order_within'] = orderWithin;
                            break;
                        }
                    }
                }
                return out;
            }"""
        )
        if not isinstance(data, dict):
            return {}
        cleaned: dict[str, str] = {}
        for k, v in data.items():
            if not v:
                continue
            if k in {"shipping", "pickup", "delivery"}:
                cleaned[k] = _humanize_status(str(v)) or str(v)
            elif k in {"shipping_eta", "pickup_eta", "delivery_eta", "shipping_order_within"}:
                cleaned[k] = str(v).strip()[:200]
        return cleaned
    except Exception:
        return {}


async def _text(page: Page, selectors: list[str], timeout: int = 2500) -> str | None:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count():
                t = (await el.inner_text(timeout=timeout)).strip()
                if t:
                    return t
        except Exception:
            continue
    return None


async def _attr(page: Page, selectors: list[str], attr: str) -> str | None:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count():
                v = await el.get_attribute(attr)
                if v:
                    return v.strip()
        except Exception:
            continue
    return None


def empty_row(item_id: str, zip_code: str, url: str) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "zip_code": zip_code,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "title": None,
        "brand": None,
        "price": None,
        "was_price": None,
        "currency": "USD",
        "availability": None,
        "shipping": None,
        "shipping_eta": None,
        "shipping_order_within": None,
        "pickup": None,
        "pickup_eta": None,
        "delivery": None,
        "delivery_eta": None,
        "rating": None,
        "reviews_count": None,
        "model": None,
        "upc": None,
        "gtin": None,
        "category": None,
        "description": None,
        "image_url": None,
        "seller": None,
        "url": url,
        "http_status": None,
        "status": "Failed",
        "error": None,
    }


# ---------------------------------------------------------------------------
# Scrape one product
# ---------------------------------------------------------------------------


async def scrape_walmart_item(
    context: BrowserContext, page: Page, item_id: str, zip_code: str
) -> dict[str, Any]:
    url = f"https://www.walmart.com/ip/{item_id}"
    print(f"[SCRAPE] Item {item_id} | ZIP {zip_code}")
    data = empty_row(item_id, zip_code, url)

    try:
        # ZIP cookies before opening product (delivery times depend on this)
        await context.add_cookies(make_location_cookies(zip_code))
        await asyncio.sleep(random.uniform(0.8, 1.5))

        response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        data["http_status"] = response.status if response else None

        if data["http_status"] == 404:
            data["status"] = "Not Found"
            data["error"] = "404"
            return data

        await asyncio.sleep(2.0)
        try:
            await page.wait_for_selector(
                "#__NEXT_DATA__, h1, script[type='application/ld+json']", timeout=8000
            )
        except Exception:
            pass

        html = await page.content()
        title = await page.title()
        if is_challenge_html(html, title):
            print("[BLOCK] Captcha page — complete in Chrome window...")
            passed = await wait_out_challenge(page, config.CAPTCHA_WAIT_SECONDS)
            if not passed:
                data["error"] = "Captcha / anti-bot challenge (Robot or human?)"
                await asyncio.sleep(config.BLOCK_COOLDOWN_SECONDS)
                return data
            html = await page.content()

        # Re-set ZIP on this product page so tiles show Arrives today / As soon as...
        print(f"[LOCATION] Applying ZIP {zip_code} on product page...")
        await ensure_zip_on_product_page(page, zip_code)
        await asyncio.sleep(1.5)
        try:
            await page.wait_for_selector("text=Shipping", timeout=8000)
        except Exception:
            pass

        html = await page.content()

        # Wrong-product guard (Walmart sometimes shows another item)
        page_item = _page_item_id_from_html(html)
        if page_item and str(page_item) != str(item_id) and str(item_id) not in str(page_item):
            print(f"[WARN] Page item {page_item} != requested {item_id}. Reloading...")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2.0)
            await ensure_zip_on_product_page(page, zip_code)
            await asyncio.sleep(1.5)
            html = await page.content()
            page_item = _page_item_id_from_html(html)
            if page_item and str(page_item) != str(item_id) and str(item_id) not in str(page_item):
                data["error"] = f"Wrong product loaded ({page_item})"
                data["status"] = "Failed"
                return data

        parsed: dict[str, Any] = {}
        parsed.update(_parse_json_ld(html))
        parsed.update(_parse_next_data(html))
        # Prefer DOM fulfillment over JSON stock codes
        for k, v in parsed.items():
            if k.startswith(("shipping", "pickup", "delivery")):
                continue
            if v and not data.get(k):
                data[k] = v
        # Keep JSON fulfillment only as weak fallback later
        json_ff = {
            k: v
            for k, v in parsed.items()
            if k.startswith(("shipping", "pickup", "delivery")) and v
        }

        if not data["title"]:
            data["title"] = await _text(
                page,
                ['h1[itemprop="name"]', '[data-automation-id="product-title"]', "h1"],
            )
            if data["title"] and "robot or human" in data["title"].lower():
                data["title"] = None

        if not data["price"]:
            data["price"] = await _text(
                page,
                [
                    '[itemprop="price"]',
                    '[data-testid="price-wrap"]',
                    '[data-automation-id="product-price"]',
                ],
            )

        if not data["brand"]:
            data["brand"] = await _text(page, ['a[href*="/brand/"]', '[itemprop="brand"]'])

        if not data["image_url"]:
            data["image_url"] = await _attr(
                page,
                ['img[data-testid="hero-image"]', 'img[itemprop="image"]'],
                "src",
            )

        if not data["description"]:
            data["description"] = _strip_html(
                await _text(
                    page,
                    [
                        '[data-testid="product-description-content"]',
                        'div[itemprop="description"]',
                    ],
                )
            )

        # PRIMARY: visible fulfillment cards (screenshot text)
        dom_ff = await fulfillment.click_fulfillment_and_reread(page)
        fulfillment.merge_fulfillment(data, dom_ff)
        if not fulfillment.fulfillment_looks_complete(data):
            more = await fulfillment.extract_fulfillment_tiles(page)
            fulfillment.merge_fulfillment(data, more)
        # Weak JSON fallback last
        fulfillment.merge_fulfillment(data, json_ff)

        if data.get("title"):
            data["status"] = "Success"
            data["error"] = None
            print(
                f"[OK] {data['title'][:50]} | {data.get('price')} | "
                f"Ship: {data.get('shipping_eta') or data.get('shipping') or '-'} "
                f"{('['+data.get('shipping_order_within')+']') if data.get('shipping_order_within') else ''} | "
                f"Pick: {data.get('pickup_eta') or data.get('pickup') or '-'} | "
                f"Del: {data.get('delivery_eta') or data.get('delivery') or '-'}"
            )
        else:
            data["status"] = "Partial"
            data["error"] = "Product JSON/title not found"
            print(f"[PARTIAL] No title for {item_id}")

    except Exception as e:
        data["error"] = str(e)
        print(f"[ERROR] {item_id} @ {zip_code}: {e}")

    return data


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------


def chrome_start_script() -> str:
    """Platform-specific helper script name (repo root)."""
    return "start_chrome.bat" if sys.platform == "win32" else "start_chrome.sh"


def find_chrome_executable() -> Path | None:
    candidates: list[Path] = []
    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
    elif sys.platform == "darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    else:
        candidates = [
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/chromium-browser"),
            Path("/usr/bin/chromium"),
            Path("/snap/bin/chromium"),
        ]
    for path in candidates:
        if path.exists():
            return path
    return None


def debug_port_open() -> bool:
    host = "127.0.0.1"
    port = config.CHROME_DEBUG_PORT
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def _clear_chrome_profile_locks(profile: Path) -> None:
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            (profile / name).unlink(missing_ok=True)
        except OSError:
            pass


def _quit_chrome_processes() -> None:
    if sys.platform == "darwin":
        subprocess.run(
            ["osascript", "-e", 'tell application "Google Chrome" to quit'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        time.sleep(1)
        for name in ("Google Chrome", "Google Chrome Helper"):
            subprocess.run(
                ["killall", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    elif sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/IM", "chrome.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        subprocess.run(
            ["killall", "chrome", "google-chrome", "chromium"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    time.sleep(1.5)


def launch_debug_chrome() -> None:
    chrome = find_chrome_executable()
    if not chrome:
        raise FileNotFoundError(
            "Google Chrome not found. Install Chrome or set Chrome path manually."
        )

    profile = Path(config.USER_DATA_DIR).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    _quit_chrome_processes()
    _clear_chrome_profile_locks(profile)

    url = "https://www.walmart.com/"
    port = str(config.CHROME_DEBUG_PORT)
    print(f"[BROWSER] Starting Chrome: {chrome.name} (port {port})")

    if sys.platform == "darwin":
        subprocess.Popen(
            [
                "open",
                "-na",
                "Google Chrome",
                "--args",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-session-crashed-bubble",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return

    cmd = [
        str(chrome),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


async def wait_for_debug_port(seconds: int = 45) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if debug_port_open():
            return True
        await asyncio.sleep(0.75)
    return False


async def connect_existing_chrome(p) -> tuple[Browser, BrowserContext, Page, bool]:
    """Connect to Chrome on debug port (auto-launches if needed)."""
    print(f"[BROWSER] Connecting to {config.CHROME_DEBUG_URL} ...")

    if not debug_port_open() and config.AUTO_LAUNCH_CHROME:
        print("[BROWSER] Chrome debug port not open — launching Chrome now...")
        try:
            launch_debug_chrome()
        except Exception as e:
            print(f"[BROWSER] Could not launch Chrome: {e}")
            print(f"[BROWSER] Or run {chrome_start_script()} manually.")
            raise SystemExit(1) from e
        print("[BROWSER] Waiting for Chrome to start...")
        if not await wait_for_debug_port(45):
            print("[BROWSER] Chrome did not open debug port in time.")
            print(f"[BROWSER] Close ALL Chrome windows, then run {chrome_start_script()} or scraper.py again.")
            raise SystemExit(1)

    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            try:
                browser = await p.chromium.connect_over_cdp(
                    config.CHROME_DEBUG_URL, no_defaults=True
                )
            except TypeError:
                browser = await p.chromium.connect_over_cdp(config.CHROME_DEBUG_URL)
            if not browser.contexts:
                raise RuntimeError(
                    "Chrome has no open window/context. Keep Chrome open after start_chrome."
                )
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            print("[BROWSER] Connected to Chrome.")
            return browser, context, page, True
        except Exception as e:
            last_error = e
            if attempt < 5:
                await asyncio.sleep(1.5)

    print(f"[BROWSER] Could not connect: {last_error}")
    print("[BROWSER] Fix:")
    print("  1) Quit ALL Chrome (Cmd+Q on Mac)")
    print(f"  2) Run {chrome_start_script()}  OR  run python3 scraper.py again")
    print("  3) Pass Walmart captcha in that Chrome window")
    if last_error and "setDownloadBehavior" in str(last_error):
        print("[BROWSER] Also upgrade Playwright:  pip3 install -U 'playwright>=1.60'")
    raise SystemExit(1) from last_error


async def create_context(p) -> tuple[BrowserContext, Page, Browser | None, bool]:
    profile = Path(config.USER_DATA_DIR)
    profile.mkdir(parents=True, exist_ok=True)

    args = ["--disable-blink-features=AutomationControlled"]
    if config.HIDE_BROWSER_WINDOW and not config.HEADLESS:
        args.extend(["--window-position=-32000,-32000", "--start-minimized"])

    launch_kwargs: dict[str, Any] = {
        "user_data_dir": str(profile.resolve()),
        "headless": config.HEADLESS,
        "slow_mo": config.SLOW_MO_MS,
        "args": args,
        "ignore_default_args": ["--enable-automation"],
        "viewport": {"width": 1366, "height": 768},
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
    }
    if config.PROXY_SERVER:
        launch_kwargs["proxy"] = {"server": config.PROXY_SERVER}
        print(f"[PROXY] Using {config.PROXY_SERVER}")
    else:
        print("[WARN] No proxy. Large runs may get blocked again.")

    channel = "chrome" if config.USE_SYSTEM_CHROME else None
    try:
        if channel:
            context = await p.chromium.launch_persistent_context(channel=channel, **launch_kwargs)
        else:
            context = await p.chromium.launch_persistent_context(**launch_kwargs)
    except Exception as e:
        print(f"[BROWSER] Launch failed: {e}")
        raise

    page = context.pages[0] if context.pages else await context.new_page()
    await apply_stealth(page)
    print("[BROWSER] Playwright Chrome (captcha often fails here — prefer start_chrome.bat).")
    return context, page, None, False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    try:
        item_ids = load_list(config.ITEM_IDS_FILE, ["item_id", "id", "Item ID", "ITEM_ID"])
        zip_codes = load_list(config.ZIP_CODES_FILE, ["zip_code", "zip", "ZIP", "ZIP 1", "ZIP1"])
    except Exception as e:
        print(f"[FATAL] Could not load inputs: {e}")
        sys.exit(1)

    if config.MAX_ITEMS is not None:
        item_ids = item_ids[: config.MAX_ITEMS]
    if config.MAX_ZIPS is not None:
        zip_codes = zip_codes[: config.MAX_ZIPS]

    print(f"[INPUT] {len(item_ids)} item IDs | {len(zip_codes)} ZIP codes")
    print(f"[INPUT] Total jobs: {len(item_ids) * len(zip_codes)}")

    out_path = Path(config.OUTPUT_FILE)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results = load_results(out_path)
    done_keys = successful_keys(results) if config.RESUME_FROM_CHECKPOINT else set()
    if getattr(config, "FORCE_RESCRAPE_ALL", False):
        print("[RESUME] FORCE_RESCRAPE_ALL=True — scraping all jobs again.")
        done_keys = set()
        results = []
    elif done_keys:
        print(f"[RESUME] Skipping {len(done_keys)} already-successful rows. Failed rows will be retried.")

    async with async_playwright() as p:
        cdp_browser: Browser | None = None
        using_cdp = False

        if config.CONNECT_EXISTING_CHROME:
            cdp_browser, context, page, using_cdp = await connect_existing_chrome(p)
        else:
            context, page, _, using_cdp = await create_context(p)

        try:
            if not await manual_warmup(page):
                return

            for z_i, zip_code in enumerate(zip_codes, start=1):
                print(f"\n===== ZIP {z_i}/{len(zip_codes)}: {zip_code} =====")
                await set_walmart_location(context, page, zip_code)

                for i_i, item_id in enumerate(item_ids, start=1):
                    key = job_key(item_id, zip_code)
                    if key in done_keys:
                        print(f"[SKIP] Already success: {item_id} @ {zip_code}")
                        continue

                    print(f"  Item {i_i}/{len(item_ids)}")
                    row = empty_row(item_id, zip_code, f"https://www.walmart.com/ip/{item_id}")
                    for attempt in range(1, config.MAX_RETRIES_PER_ITEM + 1):
                        row = await scrape_walmart_item(context, page, item_id, zip_code)
                        if row.get("status") == "Success" and fulfillment.fulfillment_looks_complete(row):
                            break
                        if row.get("status") == "Success":
                            # Have title but weak fulfillment — one more try
                            print(f"[RETRY] Fulfillment incomplete for {item_id}, attempt {attempt + 1}")
                        elif attempt < config.MAX_RETRIES_PER_ITEM:
                            print(f"[RETRY] {item_id} attempt {attempt + 1}/{config.MAX_RETRIES_PER_ITEM}")
                        await asyncio.sleep(random.uniform(2, 4))
                        if attempt >= config.MAX_RETRIES_PER_ITEM:
                            break

                    results = upsert_result(results, row)
                    save_results(results, out_path)
                    if row.get("status") == "Success":
                        done_keys.add(key)

                    await asyncio.sleep(
                        random.uniform(config.DELAY_BETWEEN_ITEMS_MIN, config.DELAY_BETWEEN_ITEMS_MAX)
                    )

                await asyncio.sleep(
                    random.uniform(config.DELAY_BETWEEN_ZIPS_MIN, config.DELAY_BETWEEN_ZIPS_MAX)
                )
        finally:
            if using_cdp and cdp_browser:
                await cdp_browser.close()  # disconnect only; Chrome stays open
            elif not using_cdp:
                await context.close()

    print(f"\n[DONE] Saved -> {out_path.resolve()}")
    print(f"[DONE] Rows: {len(results)}")


if __name__ == "__main__":
    asyncio.run(main())
