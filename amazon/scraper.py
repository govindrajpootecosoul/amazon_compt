"""
Amazon product scraper (geographic marketplaces).

Headless background mode by default (no visible browser).

From India: set a residential proxy for the target country in amazon/config.py
(PROXIES["us"] / uk / de / ca). Without it the scraper refuses to start so your
home IP is not burned.

Put ASINs into amazon/inputs/{us,uk,de,ca}_asins.txt

Run:
  python amazon/scraper.py us
  python amazon/scraper.py ae      # UAE amazon.ae
  python amazon/scraper.py all     # every marketplace in config

Output: amazon/output/{YYYY-MM-DD_HHMMSS}/amazon_{geo}_data.xlsx
(new dated folder + file every run; multiple offers = multiple rows)
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
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

# Ensure amazon/ is on path when run as python amazon/scraper.py
_AMAZON_DIR = Path(__file__).resolve().parent
if str(_AMAZON_DIR) not in sys.path:
    sys.path.insert(0, str(_AMAZON_DIR))
os.chdir(_AMAZON_DIR)

import config

COLUMNS = [
    "asin",
    "marketplace",
    "scraped_at",
    "title",
    "brand",
    "price",
    "was_price",
    "currency",
    "availability",
    "offer_index",
    "offer_count",
    "is_buybox_winner",
    "buybox_available",
    "buybox_price",
    "buybox_owner",
    "buybox_ships_from",
    "buybox_fulfilled_by",
    "buybox_is_amazon",
    "buybox_condition",
    "buybox_delivery",
    "rating",
    "reviews_count",
    "category",
    # Product information — Item details
    "recommended_uses",
    "number_of_pieces",
    "included_components",
    "item_type_name",
    "reusability",
    "gtin",
    "manufacturer",
    "upc",
    "item_highlight",
    "unit_count",
    "model_number",
    "manufacturer_part_number",
    "best_sellers_rank",
    # Style
    "color",
    "style_name",
    "shape",
    "pattern",
    "finish_types",
    "theme",
    "occasion_type",
    "seasons",
    # Materials & Care
    "material_type",
    "product_care_instructions",
    "material_features",
    # Measurements
    "item_dimensions",
    "size",
    "item_weight",
    "net_content_weight",
    # Features & Specs
    "special_features",
    # Catch-all for any extra detail rows
    "product_details_raw",
    "features",
    "description",
    "image_url",
    "seller",
    "ships_from",
    "url",
    "http_status",
    "status",
    "error",
]

# Amazon "Product information" label → Excel column
PRODUCT_DETAIL_LABEL_MAP = {
    "brand name": "brand",
    "brand": "brand",
    "recommended uses for product": "recommended_uses",
    "recommended uses": "recommended_uses",
    "number of pieces": "number_of_pieces",
    "included components": "included_components",
    "item type name": "item_type_name",
    "item type": "item_type_name",
    "reusability": "reusability",
    "global trade identification number": "gtin",
    "gtin": "gtin",
    "manufacturer": "manufacturer",
    "upc": "upc",
    "item highlight": "item_highlight",
    "unit count": "unit_count",
    "model number": "model_number",
    "model": "model_number",
    "item model number": "model_number",
    "manufacturer part number": "manufacturer_part_number",
    "part number": "manufacturer_part_number",
    "best sellers rank": "best_sellers_rank",
    "asin": "asin",
    "customer reviews": "customer_reviews_text",
    "color": "color",
    "colour": "color",
    "style name": "style_name",
    "style": "style_name",
    "shape": "shape",
    "pattern": "pattern",
    "finish types": "finish_types",
    "finish type": "finish_types",
    "theme": "theme",
    "occasion type": "occasion_type",
    "occasion": "occasion_type",
    "seasons": "seasons",
    "material type": "material_type",
    "material": "material_type",
    "product care instructions": "product_care_instructions",
    "care instructions": "product_care_instructions",
    "material features": "material_features",
    "item dimensions l x w x thickness": "item_dimensions",
    "item dimensions lxwxh": "item_dimensions",
    "product dimensions": "item_dimensions",
    "item dimensions": "item_dimensions",
    "dimensions": "item_dimensions",
    "size": "size",
    "item weight": "item_weight",
    "weight": "item_weight",
    "net content weight": "net_content_weight",
    "other special features of the product": "special_features",
    "special features": "special_features",
    "special feature": "special_features",
}


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
# Inputs / outputs
# ---------------------------------------------------------------------------


def marketplace_cfg(code: str) -> dict[str, Any]:
    key = code.strip().lower()
    if key not in config.MARKETPLACES:
        raise ValueError(
            f"Unknown marketplace '{code}'. Use one of: {', '.join(config.MARKETPLACES)}"
        )
    return config.MARKETPLACES[key]


def product_url(domain: str, asin: str) -> str:
    return f"https://{domain}/dp/{asin}"


def resolve_proxy(marketplace: str, raw_override: str | None = None) -> dict[str, str] | None:
    """Return Playwright proxy dict, or None if unset."""
    raw = raw_override
    if not raw:
        proxies = getattr(config, "PROXIES", None) or {}
        if isinstance(proxies, dict):
            raw = proxies.get(marketplace) or proxies.get(marketplace.lower())
        if not raw:
            raw = getattr(config, "PROXY_SERVER", None)
    if not raw:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    # Playwright accepts server + optional username/password
    # Support http://user:pass@host:port
    m = re.match(
        r"^(?P<scheme>https?|socks5)://(?:(?P<user>[^:@]+):(?P<pw>[^@]*)@)?(?P<host>.+)$",
        raw,
        re.I,
    )
    if not m:
        return {"server": raw}
    server = f"{m.group('scheme')}://{m.group('host')}"
    out: dict[str, str] = {"server": server}
    if m.group("user"):
        out["username"] = m.group("user")
        out["password"] = m.group("pw") or ""
    return out


def resolve_worker_proxy(marketplace: str, worker_id: int) -> dict[str, str] | None:
    """Proxy for a parallel worker: PARALLEL_PROXIES[i] or marketplace proxy."""
    plist = getattr(config, "PARALLEL_PROXIES", None) or []
    plist = [p for p in plist if p]  # drop blanks / comments placeholders
    if plist:
        raw = plist[worker_id % len(plist)]
        return resolve_proxy(marketplace, raw_override=str(raw))
    return resolve_proxy(marketplace)


def worker_count(asin_count: int | None = None) -> int:
    """
    How many Chrome workers to open.
    - If WORKERS is a positive int → use that (fixed).
    - Else if ASINs_PER_WORKER set → ceil(asins / 10) capped by MAX_WORKERS.
    """
    import math

    max_w = int(getattr(config, "MAX_WORKERS", 15) or 15)
    max_w = max(1, max_w)

    fixed = getattr(config, "WORKERS", None)
    if fixed is not None and str(fixed).strip() != "":
        try:
            n = int(fixed)
            if n >= 1:
                return min(n, max_w)
        except (TypeError, ValueError):
            pass

    per = getattr(config, "ASINs_PER_WORKER", None)
    if per and asin_count is not None:
        try:
            per_i = max(1, int(per))
        except (TypeError, ValueError):
            per_i = 10
        n = max(1, int(math.ceil(asin_count / float(per_i))))
        return min(n, max_w)

    if per:
        # asin count unknown yet — at least 1; parallel path will recompute
        return min(2, max_w)

    return 1


def split_asins_round_robin(asins: list[str], n: int) -> list[list[str]]:
    chunks: list[list[str]] = [[] for _ in range(n)]
    for i, asin in enumerate(asins):
        chunks[i % n].append(asin)
    return chunks


def split_asins_fixed_size(asins: list[str], per: int) -> list[list[str]]:
    """Each chunk has up to `per` ASINs (last chunk may be smaller)."""
    per = max(1, int(per))
    return [asins[i : i + per] for i in range(0, len(asins), per)]


def ensure_proxy_or_exit(marketplace: str, mcfg: dict[str, Any]) -> dict[str, str] | None:
    proxy = resolve_proxy(marketplace)
    require = getattr(config, "REQUIRE_PROXY", True)
    country = mcfg.get("proxy_country", marketplace.upper())
    if proxy:
        # Do not print password
        print(f"[PROXY] OK — using geo proxy for {country} via {proxy['server']}")
        return proxy
    if require:
        print("\n[FATAL] No proxy configured — refusing to scrape from your India IP.")
        print("  Amazon US/UK/DE/CA from India without a residential proxy will:")
        print("    - show captcha / robot check")
        print("    - redirect to amazon.in")
        print("    - risk blocking your home IP")
        print(f"\n  Fix: set PROXIES['{marketplace}'] in amazon/config.py")
        print(f'  Example: PROXIES["{marketplace}"] = "http://USER:PASS@host:port"')
        print(f"  Use a {country} residential proxy (same country as the marketplace).")
        print("\n  For a risky local test only: set REQUIRE_PROXY = False (not recommended).")
        raise SystemExit(2)
    print(
        f"[WARN] No proxy — scraping {marketplace.upper()} from India IP. "
        "High chance of captcha/block. Keep MAX_ASINS small."
    )
    return None


def _read_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    values: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for part in re.split(r"[\s,;|]+", line):
            part = part.strip().strip('"').strip("'").upper()
            if part and part.lower() not in {"asin", "asins", "id"}:
                values.append(part)
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def load_asins(path_str: str) -> list[str]:
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"ASIN file not found: {path.resolve()}")
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str)
        col = next((c for c in ["asin", "ASIN", "Asin"] if c in df.columns), df.columns[0])
        values = df[col].dropna().astype(str).str.strip().str.upper().tolist()
        seen: set[str] = set()
        out: list[str] = []
        for v in values:
            if v and v.lower() != "asin" and v not in seen:
                seen.add(v)
                out.append(v)
        return out
    return _read_lines(path)


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
    Save to the main Excel path. If that file is open in Excel (Permission denied),
    overwrite a single fixed autosave file instead of creating a new file every time.
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

    # One fixed autosave file (reused) — avoids dozens of timestamped copies
    alt = path.with_name(f"{path.stem}_autosave{path.suffix}")
    try:
        _write(alt)
        print(f"[SAVE] Could not overwrite {path.name} (close Excel!).")
        print(f"[SAVE] Updating single autosave file: {alt.name}")
        return alt
    except Exception as e:
        print(f"[SAVE] FATAL save error: {last_err or e}")
        raise


def make_run_output_path(
    marketplace: str, run_folder: Path | None = None
) -> Path:
    """
    Always create a separate Excel for this run.
    Example: output/2026-09-11_095830/amazon_us_data.xlsx

    If run_folder is passed (e.g. when scraping all geos in one cmd),
    all files share that same date-time folder.
    """
    root = Path(getattr(config, "OUTPUT_DIR", "output"))
    if run_folder is None:
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        if getattr(config, "OUTPUT_FOLDER_BY_DATETIME", True):
            run_folder = root / stamp
        else:
            run_folder = root
    run_folder.mkdir(parents=True, exist_ok=True)
    return run_folder / f"amazon_{marketplace}_data.xlsx"


def upsert_result(results: list[dict[str, Any]], row: dict[str, Any]) -> list[dict[str, Any]]:
    """Replace all rows for this ASIN with the new row(s)."""
    return upsert_asin_rows(results, str(row.get("asin", "")), [row])


def upsert_asin_rows(
    results: list[dict[str, Any]], asin: str, new_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    key = str(asin).upper()
    out = [r for r in results if str(r.get("asin", "")).upper() != key]
    out.extend(new_rows)
    return out


def successful_asins(results: list[dict[str, Any]]) -> set[str]:
    """ASINs that should not be scraped again: Success (with buy box fields) or Skip."""
    if getattr(config, "FORCE_RESCRAPE_ALL", False):
        return set()
    keys: set[str] = set()
    for r in results:
        status = str(r.get("status", "")).lower()
        asin = str(r.get("asin", "")).upper()
        if not asin:
            continue
        if status in {"skip", "notfound"}:
            keys.add(asin)
            continue
        if status == "success" and r.get("title"):
            # Done once buy box status is filled (Yes / no buybox / No)
            if r.get("buybox_available"):
                keys.add(asin)
    return keys


def _norm_price_key(price: Any) -> str:
    """Normalize prices for matching ('40.80' == '40.8' == 'AED 40.80')."""
    if price is None:
        return ""
    s = re.sub(r"[^0-9.]", "", str(price))
    if not s or s == ".":
        return ""
    try:
        return f"{float(s):.2f}"
    except Exception:
        return s


def _dedupe_seller_phrase(t: str) -> str:
    """
    Fix garbled owners like:
      'Day To Day … Day To Day … Sold by Day To Day …'
      'TGMarket AE TGMarket AE Sold by TGMarket AE'
    """
    t = re.sub(r"\s+", " ", (t or "")).strip()
    if not t:
        return t
    # Strip every leading "Sold by"
    for _ in range(3):
        nt = re.sub(
            r"^(sold by|ships from|fulfilled by|delivered by)\s*[:\-]?\s*",
            "",
            t,
            flags=re.I,
        ).strip()
        if nt == t:
            break
        t = nt
    # Collapse exact repeated halves: "Name Name" / "Name Name Name"
    for _ in range(4):
        m = re.match(r"^(.+?)\s+\1(?:\s+\1)*$", t, flags=re.I)
        if not m:
            break
        t = m.group(1).strip()
    # "Name Sold by Name" or "Name Name Sold by Name"
    m = re.match(r"^(.+?)(?:\s+\1)?\s+sold by\s+\1$", t, flags=re.I)
    if m:
        return m.group(1).strip()
    m = re.match(r"^(.+?)\s+sold by\s+(.+)$", t, flags=re.I)
    if m:
        left, right = m.group(1).strip(), m.group(2).strip()
        if left.lower() == right.lower():
            return left
        if right.lower() in left.lower():
            return right
        if left.lower() in right.lower():
            return left
        # Prefer the shorter clean merchant token after Sold by
        return right if len(right) <= len(left) else left
    # Trailing "Sold by …" junk
    t = re.sub(r"\s+sold by\s+.*$", "", t, flags=re.I).strip()
    return t


def _clean_seller_name(text: str | None) -> str | None:
    if not text:
        return None
    t = re.sub(r"\s+", " ", str(text)).strip()
    t = _dedupe_seller_phrase(t)
    t = re.sub(
        r"^(sold by|ships from|fulfilled by|delivered by|dispatched from|"
        r"shipper\s*/\s*seller|brand:|seller:|merchant:|بيع من|يشحن من|تم التوصيل بواسطة)\s*[:\-]?\s*",
        "",
        t,
        flags=re.I,
    ).strip()
    t = _dedupe_seller_phrase(t)
    # Collapse noisy Amazon fulfillment phrases → canonical owner name
    if re.search(r"\bamazon\.ae\b", t, re.I):
        if re.match(
            r"^(amazon\.ae|amazon\s*ae)\b",
            t,
            re.I,
        ) or re.search(
            r"^(ships from|delivered by|fulfilled by|sold by)?\s*amazon\.ae\b",
            t,
            re.I,
        ):
            return "Amazon.ae"
    if re.search(r"amazon.*fulfilled by amazon", t, re.I):
        if re.search(r"amazon\.ae", t, re.I):
            return "Amazon.ae"
        return "Amazon"
    if re.fullmatch(r"amazon(\.com)?", t, re.I):
        return "Amazon"
    if re.fullmatch(r"amazon\.ae", t, re.I):
        return "Amazon.ae"
    # Strip AOD UI chrome
    t = re.sub(r"\s*see\s+(less|more|all)(\s+buying\s+options)?\b.*$", "", t, flags=re.I).strip()
    t = re.sub(r"\s*\.+\s*more\s*$", "", t, flags=re.I).strip()
    t = re.sub(r"\s+details\s*$", "", t, flags=re.I).strip()
    t = re.sub(r"\s*\d(?:\.\d)?\s*out of\s*5.*$", "", t, flags=re.I).strip()
    t = re.sub(r"\s*\(?\d[\d,]*\s*ratings?\)?\s*$", "", t, flags=re.I).strip()
    t = re.sub(r"\s*[\d]+%\s*positive.*$", "", t, flags=re.I).strip()
    junk = {
        "delivered by",
        "sold by",
        "ships from",
        "fulfilled by",
        "by",
        "shipper",
        "seller",
        "shipper / seller",
        "shipper/seller",
        "shipper / seller information",
        "price",
        "condition",
        "delivery",
        "details",
        "see less",
        "see more",
        "see all",
        "see all buying options",
        "report",
        "feedback",
        "new",
        "used",
        "shop qualifying items",
        "qualifying items",
        "add to cart",
        "more",
        "... more",
        "… more",
        "learn more",
    }
    if t.lower() in junk:
        return None
    if re.fullmatch(r"\.{0,3}\s*more", t, flags=re.I):
        return None
    if re.fullmatch(r"shop\s+qualifying\s+items.*", t, flags=re.I):
        return None
    if re.fullmatch(r"shipper\s*/\s*seller.*", t, flags=re.I):
        return None
    if re.fullmatch(
        r"(sold by|ships from|fulfilled by|delivered by|amazon\.ae\s*delivery|details|see\s+less|see\s+more)?",
        t,
        flags=re.I,
    ):
        return None
    if len(t) < 2:
        return None
    t = re.sub(r"\s*\|\s*.*$", "", t).strip()
    t = _dedupe_seller_phrase(t)
    if t.lower() in junk:
        return None
    if re.fullmatch(r"amazon\.ae", t, re.I):
        return "Amazon.ae"
    if re.fullmatch(r"amazon(\.com)?", t, re.I):
        return "Amazon"
    return t or None


def _is_amazon_seller(name: str | None) -> bool:
    if not name:
        return False
    n = name.lower().strip()
    amazon_names = {
        "amazon",
        "amazon.com",
        "amazon.ca",
        "amazon.co.uk",
        "amazon.de",
        "amazon.ae",
        "amazon.com.au",
        "amazon.in",
    }
    if n in amazon_names:
        return True
    if n.startswith("amazon."):
        return True
    if "amazon.ae" in n or n == "amazon ae":
        return True
    # "Ships from Amazon", "Amazon Fulfilled", etc.
    if re.fullmatch(r"amazon(\s*\.\s*[a-z.]+)?", n):
        return True
    if re.match(r"^amazon(\.ae|\.com)?\b", n) and len(n) < 40:
        return True
    return False


def _amazon_owner_label(domain: str | None = None, sample: str | None = None) -> str:
    blob = f"{domain or ''} {sample or ''}".lower()
    if "amazon.ae" in blob or blob.strip().endswith(".ae") or ".ae" in (domain or ""):
        return "Amazon.ae"
    if "amazon.co.uk" in blob or "amazon.de" in blob or "amazon.ca" in blob:
        if "co.uk" in blob:
            return "Amazon.co.uk"
        if "amazon.de" in blob:
            return "Amazon.de"
        if "amazon.ca" in blob:
            return "Amazon.ca"
    return "Amazon"


def _finalize_offer(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if raw.get("_price_suppressed"):
        # Amazon hid the buy-box price ("Price higher than typical")
        out["buybox_available"] = "no buybox"
        out["buybox_price"] = None
        out["buybox_owner"] = _clean_seller_name(raw.get("buybox_owner"))
        out["buybox_ships_from"] = _clean_seller_name(raw.get("buybox_ships_from"))
        out["buybox_fulfilled_by"] = _clean_seller_name(raw.get("buybox_fulfilled_by"))
        out["buybox_condition"] = _as_text(raw.get("buybox_condition"))
        out["buybox_delivery"] = _as_text(raw.get("buybox_delivery"))
        out["buybox_is_amazon"] = None
        if raw.get("is_buybox_winner"):
            out["is_buybox_winner"] = raw["is_buybox_winner"]
        return out

    avail = raw.get("buybox_available")
    if avail in (None, "", "No") and not raw.get("buybox_price") and not raw.get("buybox_owner"):
        out["buybox_available"] = "no buybox"
    else:
        out["buybox_available"] = avail or (
            "Yes" if raw.get("buybox_price") or raw.get("buybox_owner") else "no buybox"
        )

    out["buybox_price"] = _clean_price(raw.get("buybox_price"))
    out["buybox_owner"] = _clean_seller_name(raw.get("buybox_owner"))
    out["buybox_ships_from"] = _clean_seller_name(raw.get("buybox_ships_from"))
    out["buybox_fulfilled_by"] = _clean_seller_name(raw.get("buybox_fulfilled_by"))
    out["buybox_condition"] = _as_text(raw.get("buybox_condition"))
    out["buybox_delivery"] = _as_text(raw.get("buybox_delivery"))

    domain = str(raw.get("_domain") or "")

    # If fulfilled/ships says Amazon but owner empty → owner = Amazon(.ae)
    for field in ("buybox_ships_from", "buybox_fulfilled_by"):
        val = out.get(field)
        if val and _is_amazon_seller(val) and not out.get("buybox_owner"):
            out["buybox_owner"] = (
                val if re.search(r"amazon\.", val, re.I) else _amazon_owner_label(domain, val)
            )
            break
    if not out.get("buybox_owner") and raw.get("_amazon_fallback"):
        out["buybox_owner"] = _clean_seller_name(str(raw.get("_amazon_fallback"))) or str(
            raw.get("_amazon_fallback")
        )

    seller = out.get("buybox_owner")
    ships = out.get("buybox_ships_from")
    fulfilled = out.get("buybox_fulfilled_by")
    if out["buybox_available"] == "no buybox":
        out["buybox_is_amazon"] = None
    elif _is_amazon_seller(seller) or _is_amazon_seller(ships) or _is_amazon_seller(fulfilled):
        out["buybox_is_amazon"] = "Yes"
    elif seller or ships:
        out["buybox_is_amazon"] = "No"
    else:
        out["buybox_is_amazon"] = None

    # Critical: Amazon offer must never leave buybox_owner blank
    if out.get("buybox_is_amazon") == "Yes" and not out.get("buybox_owner"):
        out["buybox_owner"] = _amazon_owner_label(
            domain, f"{ships or ''} {fulfilled or ''} {raw.get('_amazon_fallback') or ''}"
        )

    if raw.get("is_buybox_winner"):
        out["is_buybox_winner"] = raw["is_buybox_winner"]
    # Final pass — collapse any remaining "Name Name Sold by Name"
    if out.get("buybox_owner"):
        out["buybox_owner"] = _clean_seller_name(out["buybox_owner"])
    return out


async def extract_buybox(page: Page, domain: str | None = None) -> dict[str, Any]:
    """Extract primary buy box winner details (works on .com / .ae / etc.)."""
    try:
        raw = await page.evaluate(
            """() => {
                const out = {
                    buybox_available: null,
                    buybox_price: null,
                    buybox_owner: null,
                    buybox_ships_from: null,
                    buybox_fulfilled_by: null,
                    buybox_condition: null,
                    buybox_delivery: null,
                    _amazon_fallback: null,
                };
                const text = (el) => (el && el.textContent ? el.textContent.replace(/\\s+/g, ' ').trim() : '');
                const dedupeSeller = (t) => {
                    let s = (t || '').replace(/\\s+/g, ' ').trim();
                    if (!s) return s;
                    for (let i = 0; i < 3; i++) {
                        const n = s.replace(/^(sold by|ships from|fulfilled by|delivered by)\\s*[:\\-]?\\s*/i, '').trim();
                        if (n === s) break;
                        s = n;
                    }
                    for (let i = 0; i < 4; i++) {
                        const m = s.match(/^(.+?)\\s+\\1(?:\\s+\\1)*$/i);
                        if (!m) break;
                        s = m[1].trim();
                    }
                    let m = s.match(/^(.+?)(?:\\s+\\1)?\\s+sold by\\s+\\1$/i);
                    if (m) return m[1].trim();
                    m = s.match(/^(.+?)\\s+sold by\\s+(.+)$/i);
                    if (m) {
                        const left = m[1].trim(), right = m[2].trim();
                        if (left.toLowerCase() === right.toLowerCase()) return left;
                        if (right.toLowerCase().includes(left.toLowerCase())) return left;
                        if (left.toLowerCase().includes(right.toLowerCase())) return right;
                        return right.length <= left.length ? right : left;
                    }
                    s = s.replace(/\\s+sold by\\s+.*$/i, '').trim();
                    s = s.replace(/\\s+(?:ships from|delivered by|fulfilled by)\\s+.*$/i, '').trim();
                    return s;
                };
                const cleanLabelValue = (t) => {
                    if (!t) return null;
                    let s = dedupeSeller(t);
                    s = s.replace(/^(sold by|ships from|fulfilled by|delivered by|dispatched from)\\s*[:\\-]?\\s*/i, '').trim();
                    s = dedupeSeller(s);
                    s = s.replace(/\\s*see\\s+(less|more|all)(\\s+buying\\s+options)?\\b.*$/i, '').trim();
                    s = s.replace(/\\s+details\\s*$/i, '').trim();
                    if (!s || /^(sold by|ships from|fulfilled by|delivered by|details|see\\s+less|see\\s+more|shop\\s+qualifying\\s+items)$/i.test(s)) return null;
                    if (/^amazon\\.ae\\b/i.test(s)) return 'Amazon.ae';
                    if (/^amazon\\b/i.test(s) && s.length < 20) return s.match(/^amazon(?:\\.[a-z.]+)?/i)[0];
                    return s;
                };
                const amazonLabel = () => {
                    const host = (location.hostname || '').toLowerCase();
                    if (host.includes('amazon.ae')) return 'Amazon.ae';
                    if (host.includes('amazon.co.uk')) return 'Amazon.co.uk';
                    if (host.includes('amazon.de')) return 'Amazon.de';
                    if (host.includes('amazon.ca')) return 'Amazon.ca';
                    return 'Amazon';
                };

                const addBtn =
                    document.querySelector('#add-to-cart-button') ||
                    document.querySelector('#submit.add-to-cart') ||
                    document.querySelector('input#add-to-cart-button') ||
                    document.querySelector('#buy-now-button') ||
                    document.querySelector('input[name="submit.add-to-cart"]');
                out.buybox_available = addBtn ? 'Yes' : 'No';

                const buyboxZone = document.querySelector(
                    '#desktop_buybox, #buybox, #rightCol, #apex_desktop, #corePrice_feature_div, #corePriceDisplay_desktop_feature_div'
                );
                const zoneTxt = ((buyboxZone && buyboxZone.innerText) || '').slice(0, 8000);
                const priceSuppressed =
                    /price\\s+higher\\s+than\\s+typical/i.test(zoneTxt) ||
                    /we have recently seen better prices/i.test(zoneTxt);

                if (priceSuppressed) {
                    // Do not scrape carousel / "options from AED …" as the buy-box price
                    out.buybox_price = null;
                    out._price_suppressed = true;
                    out.buybox_available = 'no buybox';
                } else {
                    const priceRoots = [
                        '#price_inside_buybox',
                        '#corePrice_feature_div .a-price:not(.a-text-price) span.a-offscreen',
                        '#corePriceDisplay_desktop_feature_div .a-price:not(.a-text-price) span.a-offscreen',
                        '#apex_desktop .a-price:not(.a-text-price) span.a-offscreen',
                        '#tp_price_block_total_price_ww span.a-offscreen',
                        '#newBuyBoxPrice',
                    ];
                    for (const sel of priceRoots) {
                        const el = document.querySelector(sel);
                        if (el && text(el)) { out.buybox_price = text(el); break; }
                    }
                }

                // Direct seller link FIRST (best signal — before tabular grabs concatenated junk)
                const sellerLink = document.querySelector(
                    '#sellerProfileTriggerId, a#sellerProfileTriggerId, #merchant-info a[href*="seller"], a[href*="/gp/help/seller"], a[href*="/sp?seller="], a[href*="/gp/aag/main"]'
                );
                if (sellerLink && text(sellerLink)) {
                    out.buybox_owner = cleanLabelValue(text(sellerLink));
                }

                // Tabular buybox: label attribute OR sibling label text
                document.querySelectorAll(
                    '#tabular-buybox tr, #tabular_feature_div tr, #desktop_qualifiedBuyBox tr, #buybox tr'
                ).forEach((tr) => {
                    const labelEl = tr.querySelector('[tabular-attribute-name], .tabular-buybox-label, .a-text-bold, td:first-child');
                    const valueEl = tr.querySelector(
                        '.tabular-buybox-text a, .tabular-buybox-text, [tabular-attribute-name] + *, .offer-display-feature-text a, .offer-display-feature-text, td:last-child a, td:last-child span, a#sellerProfileTriggerId'
                    );
                    const label = ((labelEl && (labelEl.getAttribute('tabular-attribute-name') || labelEl.innerText)) || '').toLowerCase();
                    let rawVal = text(valueEl) || text(tr.querySelector('td:last-child'));
                    // Prefer anchor text only when present
                    const anchor = tr.querySelector('a#sellerProfileTriggerId, a[href*="seller"], a[href*="/gp/aag/main"], a[href*="/sp?"]');
                    if (anchor && text(anchor)) rawVal = text(anchor);
                    const val = cleanLabelValue(rawVal);
                    if (!val) return;
                    if (/sold|seller|merchant|بيع/.test(label) && !out.buybox_owner) out.buybox_owner = val;
                    if (/ship|dispatch|يشحن|delivery from/.test(label) && !out.buybox_ships_from) out.buybox_ships_from = val;
                    if (/fulfill|delivered by|تم التوصيل/.test(label) && !out.buybox_fulfilled_by) out.buybox_fulfilled_by = val;
                });

                // offer-display-feature blocks (common on AE / new buybox)
                document.querySelectorAll('[offer-display-feature-name], .offer-display-feature-text').forEach((el) => {
                    const name = (el.getAttribute('offer-display-feature-name') || '').toLowerCase();
                    const t = text(el);
                    const soldM = t && t.match(/sold by\\s*[:\\-]?\\s*(.+?)(?:\\s+ships from|\\s+delivered by|\\s+fulfilled by|$)/i);
                    const shipsM = t && t.match(/(?:ships from|delivered by|fulfilled by)\\s*[:\\-]?\\s*(.+?)(?:\\s+sold by|$)/i);
                    const val = cleanLabelValue(soldM ? soldM[1] : t);
                    if (!val && !shipsM) return;
                    if ((/merchant|sold|seller/.test(name) || /sold by/i.test(t)) && !out.buybox_owner) {
                        out.buybox_owner = val || cleanLabelValue(soldM && soldM[1]);
                    }
                    if ((/fulfiller|ships|dispatch/.test(name) || /ships from|delivered by/i.test(t)) && !out.buybox_ships_from) {
                        out.buybox_ships_from = cleanLabelValue(shipsM ? shipsM[1] : t);
                    }
                });

                // merchant-info / fulfiller blocks — parse full sentence
                const infoBlocks = [
                    '#merchant-info',
                    '#merchantInfoFeature_feature_div',
                    '#fulfillerInfoFeature_feature_div',
                    '#availability_feature_div',
                    '#desktop_qualifiedBuyBox',
                    '#buybox',
                    '#tabular-buybox',
                ];
                for (const sel of infoBlocks) {
                    const el = document.querySelector(sel);
                    const t = text(el);
                    if (!t) continue;
                    if (!out.buybox_owner) {
                        const link = el && el.querySelector(
                            'a#sellerProfileTriggerId, a[href*="/sp?seller="], a[href*="/gp/aag/main"], a[href*="/gp/help/seller"]'
                        );
                        if (link && text(link)) out.buybox_owner = cleanLabelValue(text(link));
                    }
                    if (!out.buybox_owner) {
                        let m = t.match(/sold by\\s+([^|.]+?)(?:\\s*\\||$|and\\s+fulfilled|\\s+ships from)/i)
                            || t.match(/بيع من\\s+([^|.]+)/);
                        if (m) out.buybox_owner = cleanLabelValue(m[1]);
                    }
                    if (!out.buybox_ships_from) {
                        let m = t.match(/ships from\\s+([^|.]+?)(?:\\s*\\||$|\\s+sold by)/i)
                            || t.match(/يشحن من\\s+([^|.]+)/);
                        if (m) out.buybox_ships_from = cleanLabelValue(m[1]);
                    }
                    if (/ships from and sold by amazon/i.test(t) || /sold by amazon\\.ae/i.test(t) || /amazon\\.ae/i.test(t) && /sold by/i.test(t)) {
                        out._amazon_fallback = /amazon\\.ae/i.test(t) ? 'Amazon.ae' : 'Amazon';
                        out.buybox_owner = out.buybox_owner || out._amazon_fallback;
                        out.buybox_ships_from = out.buybox_ships_from || out._amazon_fallback;
                        out.buybox_fulfilled_by = out.buybox_fulfilled_by || out._amazon_fallback;
                    }
                    if (/delivered by\\s+amazon/i.test(t) || /fulfilled by amazon/i.test(t)) {
                        out.buybox_fulfilled_by = out.buybox_fulfilled_by || ( /amazon\\.ae/i.test(t) ? 'Amazon.ae' : 'Amazon');
                        out.buybox_ships_from = out.buybox_ships_from || out.buybox_fulfilled_by;
                    }
                }

                // "Delivered by Amazon.ae" alone in ships/fulfilled nodes
                document.querySelectorAll(
                    '#fulfillerInfoFeature_feature_div .offer-display-feature-text, #mir-layout-DELIVERY_BLOCK, #deliveryBlockMessage'
                ).forEach((el) => {
                    const t = text(el);
                    const m = t && t.match(/(?:delivered by|fulfilled by|ships from)\\s+(.+)/i);
                    if (m) {
                        const v = cleanLabelValue(m[1]);
                        if (v) {
                            out.buybox_fulfilled_by = out.buybox_fulfilled_by || v;
                            out.buybox_ships_from = out.buybox_ships_from || v;
                            if (/amazon/i.test(v) && !out.buybox_owner) out.buybox_owner = v;
                        }
                    }
                });

                if (!out.buybox_fulfilled_by && out.buybox_ships_from) {
                    out.buybox_fulfilled_by = out.buybox_ships_from;
                }

                // Last resort: buy box present but Sold by missing / cleaned away
                // Skip inventing Amazon owner when price is suppressed (no real buy box)
                if (!out.buybox_owner && out.buybox_available === 'Yes' && !out._price_suppressed) {
                    const label = amazonLabel();
                    const pageText = (document.querySelector('#buybox, #desktop_buybox, #rightCol, #desktop_qualifiedBuyBox, #merchant-info') || document.body).innerText || '';
                    const amazonSold =
                        /sold by\\s+amazon/i.test(pageText) ||
                        /ships from and sold by amazon/i.test(pageText) ||
                        /sold by\\s+amazon\\.ae/i.test(pageText) ||
                        (/amazon\\.ae/i.test(pageText) && /sold by|ships from|delivered by|fulfilled by/i.test(pageText));
                    const shipsAmazon =
                        /ships from\\s+amazon/i.test(pageText) ||
                        /delivered by\\s+amazon/i.test(pageText) ||
                        /fulfilled by\\s+amazon/i.test(pageText);
                    if (amazonSold || (shipsAmazon && !/sold by\\s+(?!amazon)[A-Za-z]/i.test(pageText.slice(0, 2500)))) {
                        out.buybox_owner = label;
                        out._amazon_fallback = label;
                        out.buybox_ships_from = out.buybox_ships_from || label;
                        out.buybox_fulfilled_by = out.buybox_fulfilled_by || label;
                    } else if (out.buybox_ships_from && /amazon/i.test(out.buybox_ships_from)) {
                        out.buybox_owner = cleanLabelValue(out.buybox_ships_from) || label;
                        out._amazon_fallback = label;
                    } else if (out.buybox_fulfilled_by && /amazon/i.test(out.buybox_fulfilled_by)) {
                        out.buybox_owner = cleanLabelValue(out.buybox_fulfilled_by) || label;
                        out._amazon_fallback = label;
                    }
                }

                const cond = document.querySelector('#newAccordionCaption_feature_div, #buyboxAccordianCaption');
                if (cond && text(cond)) out.buybox_condition = text(cond);
                else if (document.querySelector('#newAccordionRow, #buyNew_c')) out.buybox_condition = 'New';

                const deliv = document.querySelector(
                    '#mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE, #deliveryBlockMessage, #ddmDeliveryMessage'
                );
                if (deliv) out.buybox_delivery = text(deliv);

                const avail = text(document.querySelector('#availability, #availability span, #outOfStock'));
                if (/cannot be shipped|currently unavailable|out of stock/i.test(avail || '')) {
                    if (!addBtn) out.buybox_available = 'No';
                }
                return out;
            }"""
        )
    except Exception:
        raw = {}

    if not isinstance(raw, dict):
        return {"buybox_available": "no buybox"}
    if domain:
        raw["_domain"] = domain
    else:
        try:
            raw["_domain"] = (page.url or "").split("/")[2] if page.url else ""
        except Exception:
            raw["_domain"] = ""
    out = _finalize_offer(raw)
    out["is_buybox_winner"] = (
        "Yes" if out.get("buybox_available") not in {None, "no buybox", "No"} else "No"
    )
    return out


async def extract_aod_offers(page: Page, domain: str | None = None) -> list[dict[str, Any]]:
    """
    Parse 'Other sellers on Amazon' AOD sidebar / offer-listing.
    Each offer card must get its own Sold by name (not only the first).
    """
    try:
        await page.evaluate(
            """() => {
                const box = document.querySelector('#aod-offer-list, #aod-container, #all-offers-display');
                if (box) box.scrollTop = box.scrollHeight;
            }"""
        )
        await asyncio.sleep(0.6)
        await page.evaluate(
            """() => {
                const box = document.querySelector('#aod-offer-list, #aod-container, #all-offers-display');
                if (box) box.scrollTop = 0;
            }"""
        )
        await asyncio.sleep(0.3)
    except Exception:
        pass

    try:
        raw_list = await page.evaluate(
            """() => {
                const text = (el) => (el && el.textContent ? el.textContent.replace(/\\s+/g, ' ').trim() : '');
                const isJunkName = (s) => {
                    if (!s) return true;
                    const x = String(s).replace(/\\s+/g, ' ').trim();
                    // "More" / "... More" promo expand link — NOT a seller
                    if (/^\\.{0,3}\\s*more\\s*$/i.test(x)) return true;
                    return /^(sold by|ships from|fulfilled by|delivered by|shipper|seller|shipper\\s*\\/\\s*seller|price|condition|delivery|details|see\\s+less|see\\s+more|see\\s+all|see\\s+all\\s+buying\\s+options|report|feedback|new|used|shop\\s+qualifying\\s+items|qualifying\\s+items|add\\s+to\\s+cart|more|learn\\s+more)$/i.test(x)
                        || /^shipper\\s*\\/\\s*seller/i.test(x)
                        || /^see\\s+(less|more|all)/i.test(x)
                        || /^shop\\s+qualifying/i.test(x)
                        || /^\\.+\\s*more$/i.test(x);
                };
                const clean = (t) => {
                    if (!t) return null;
                    let s = String(t).replace(/\\s+/g, ' ').trim();
                    // Collapse "Name Name Sold by Name"
                    for (let i = 0; i < 3; i++) {
                        const n = s.replace(/^(sold by|ships from|fulfilled by|delivered by|shipper\\s*\\/\\s*seller)\\s*[:\\-]?\\s*/i, '').trim();
                        if (n === s) break;
                        s = n;
                    }
                    for (let i = 0; i < 4; i++) {
                        const m = s.match(/^(.+?)\\s+\\1(?:\\s+\\1)*$/i);
                        if (!m) break;
                        s = m[1].trim();
                    }
                    let dm = s.match(/^(.+?)(?:\\s+\\1)?\\s+sold by\\s+\\1$/i);
                    if (dm) s = dm[1].trim();
                    else {
                        dm = s.match(/^(.+?)\\s+sold by\\s+(.+)$/i);
                        if (dm) {
                            const left = dm[1].trim(), right = dm[2].trim();
                            if (left.toLowerCase() === right.toLowerCase()) s = left;
                            else if (left.toLowerCase().includes(right.toLowerCase())) s = right;
                            else if (right.toLowerCase().includes(left.toLowerCase())) s = left;
                            else s = right.length <= left.length ? right : left;
                        }
                    }
                    s = s.replace(/^(sold by|ships from|fulfilled by|delivered by|shipper\\s*\\/\\s*seller)\\s*[:\\-]?\\s*/i, '').trim();
                    s = s.replace(/\\s*see\\s+(less|more|all)(\\s+buying\\s+options)?\\b.*$/i, '').trim();
                    s = s.replace(/\\s*\\.+\\s*more\\s*$/i, '').trim();
                    s = s.replace(/\\s+details\\s*$/i, '').trim();
                    s = s.replace(/\\s*\\d(?:\\.\\d)?\\s*out of\\s*5.*$/i, '').trim();
                    s = s.replace(/\\s*\\(?\\d[\\d,]*\\s*ratings?\\)?\\s*$/i, '').trim();
                    s = s.replace(/\\s*[\\d]+%\\s*positive.*$/i, '').trim();
                    s = s.replace(/\\s*Just Arrived\\s*$/i, '').trim();
                    s = s.replace(/\\s+(?:AED|USD|GBP|EUR|CAD)\\s*[\\d.].*$/i, '').trim();
                    s = s.replace(/\\s+(?:FREE\\s+)?delivery\\b.*$/i, '').trim();
                    if (!s || isJunkName(s) || s.length < 2) return null;
                    if (/^amazon\\.ae\\b/i.test(s)) return 'Amazon.ae';
                    return s;
                };
                const isLabelOnly = (s) => isJunkName(s);
                const isSellerHref = (href) => {
                    const h = (href || '').toLowerCase();
                    return /\\/gp\\/aag\\/main|\\/sp\\?|seller=|\\/gp\\/help\\/seller|sellerprofile/i.test(h);
                };
                const pickSellerLinkNear = (node) => {
                    if (!node) return null;
                    const links = node.querySelectorAll('a');
                    for (const link of links) {
                        const raw = text(link);
                        if (!raw || isJunkName(raw)) continue;
                        const href = link.getAttribute('href') || '';
                        if (isSellerHref(href) || /sold\\s*by/i.test((link.parentElement && link.parentElement.innerText) || '')) {
                            const v = clean(raw);
                            if (v && !isJunkName(v)) return v;
                        }
                    }
                    // fallback: first non-junk link that is not promo/More
                    for (const link of links) {
                        const raw = text(link);
                        if (!raw || isJunkName(raw)) continue;
                        const href = (link.getAttribute('href') || '').toLowerCase();
                        if (/more|detail|delivery|coupon|promo|learn|cart|wishlist|signin/i.test(href + ' ' + raw)) continue;
                        const v = clean(raw);
                        if (v && !isJunkName(v) && v.length >= 2) return v;
                    }
                    return null;
                };
                const parseSoldBy = (root) => {
                    // === PRIMARY: text immediately after "Sold by" (never position-based "More") ===
                    // 1) Dedicated soldBy blocks
                    const blocks = root.querySelectorAll(
                        '[id="aod-offer-soldBy"], [id*="soldBy"], [id*="sold-by"], [id*="SoldBy"], .aod-offer-seller, #aod-offer-soldBy'
                    );
                    for (const b of blocks) {
                        const a = b.querySelector('a');
                        if (a) {
                            const v = clean(text(a));
                            if (v && !isJunkName(v)) return v;
                        }
                        const bt = text(b);
                        const m = bt.match(/sold by\\s*[:\\-]?\\s*(.+)$/i);
                        if (m) {
                            const v = clean(m[1]);
                            if (v && !isJunkName(v)) return v;
                        }
                    }

                    // 2) Find exact "Sold by" label nodes → owner is next to / after label
                    const candidates = root.querySelectorAll('span, div, td, th, label, a');
                    for (const el of candidates) {
                        const t = text(el);
                        if (!t) continue;
                        // Exact / short label only
                        if (/^sold by\\s*$/i.test(t) || /^sold by\\s*[:\\-]?\\s*$/i.test(t)) {
                            // Prefer seller link in parent row
                            const row = el.closest('div, tr, li, span') || el.parentElement;
                            const fromRow = pickSellerLinkNear(row);
                            if (fromRow) return fromRow;
                            let sib = el.nextElementSibling;
                            for (let i = 0; i < 5 && sib; i++, sib = sib.nextElementSibling) {
                                const v = pickSellerLinkNear(sib) || clean(text(sib));
                                if (v && !isJunkName(v)) return v;
                            }
                            continue;
                        }
                        // Same node: "Sold by PEAK NEST FZ LLE"
                        if (/^sold by\\s+/i.test(t) && t.length < 120) {
                            const m = t.match(/^sold by\\s*[:\\-]?\\s*(.+)$/i);
                            if (m) {
                                let v = clean(m[1]);
                                if (v) {
                                    v = v.replace(/\\s+\\d(?:\\.\\d)?\\s*out of.*$/i, '').trim();
                                    v = v.replace(/\\s+[\\d]+%\\s*positive.*$/i, '').trim();
                                    v = clean(v);
                                }
                                if (v && !isJunkName(v)) return v;
                            }
                        }
                    }

                    // 3) Full-card regex — stop before ratings / Add to cart / Delivered noise
                    const full = text(root);
                    const m2 = full.match(
                        /sold by\\s+(.+?)(?:\\s+\\d(?:\\.\\d)?\\s*out of|\\s+\\(?\\d[\\d,]*\\s*%|\\s+positive|\\s+Add to [Cc]art|\\s+Delivered by|\\s+Fulfilled by|\\s+Ships from|$)/i
                    );
                    if (m2) {
                        const v = clean(m2[1]);
                        if (v && !isJunkName(v)) return v;
                    }
                    // Arabic AE
                    const mAr = full.match(/بيع من\\s+(.+?)(?:\\s+\\d|$)/);
                    if (mAr) {
                        const v = clean(mAr[1]);
                        if (v && !isJunkName(v)) return v;
                    }
                    // Shipper / Seller value (AE table)
                    const m3 = full.match(/shipper\\s*\\/\\s*seller\\s+(.+?)(?:\\s+(?:fulfilled by|delivered by|ships from|add to cart|details|more)|\\s+\\d(?:\\.\\d)?\\s*out of|$)/i);
                    if (m3) {
                        const v = clean(m3[1]);
                        if (v && !isJunkName(v)) return v;
                    }

                    // 4) Last: seller-profile links only (never promo "More")
                    const sellerLinks = root.querySelectorAll(
                        'a[href*="/gp/aag/main"], a[href*="/sp?seller="], a[href*="seller="], a[href*="/gp/help/seller"], a#sellerProfileTriggerId'
                    );
                    for (const link of sellerLinks) {
                        const raw = text(link);
                        if (!raw || isJunkName(raw)) continue;
                        // Skip links that are not near Sold by if card has Sold by elsewhere
                        const v = clean(raw);
                        if (v && !isJunkName(v)) return v;
                    }
                    return null;
                };
                const parseShips = (root) => {
                    const full = text(root);
                    const m = full.match(/(?:ships from|delivered by|fulfilled by)\\s+(.+?)(?:\\s+sold by|\\s+Add to Cart|\\s+Details|$)/i);
                    if (m) return clean(m[1]);
                    const el = root.querySelector('[id="aod-offer-shipsFrom"], [id*="shipsFrom"]');
                    if (el) return clean(text(el));
                    return null;
                };
                const parsePrice = (root) => {
                    const off = root.querySelector('.a-price .a-offscreen, [id*="aod-price"] .a-offscreen, .aok-offscreen');
                    if (off && text(off)) return text(off);
                    const whole = root.querySelector('.a-price-whole');
                    const frac = root.querySelector('.a-price-fraction');
                    if (whole) {
                        const w = text(whole).replace(/[^0-9]/g, '');
                        const f = frac ? text(frac).replace(/[^0-9]/g, '') : '00';
                        if (w) return w + '.' + (f || '00');
                    }
                    return null;
                };

                const offers = [];
                const seen = new Set();
                const amazonLabel = () => {
                    const host = (location.hostname || '').toLowerCase();
                    if (host.includes('amazon.ae')) return 'Amazon.ae';
                    if (host.includes('amazon.co.uk')) return 'Amazon.co.uk';
                    if (host.includes('amazon.de')) return 'Amazon.de';
                    if (host.includes('amazon.ca')) return 'Amazon.ca';
                    return 'Amazon';
                };
                const pushOffer = (root, pinned) => {
                    if (!root) return;
                    const full = text(root);
                    // Skip AOD table header rows that only say Shipper / Seller
                    if (/shipper\\s*\\/\\s*seller/i.test(full) && full.length < 40 && !root.querySelector('.a-price, .a-price-whole')) {
                        return;
                    }
                    const price = parsePrice(root);
                    let seller = parseSoldBy(root);
                    if (seller && isJunkName(seller)) seller = null;
                    const ships = parseShips(root);
                    let fulfilled = null;
                    if (/fulfilled by amazon|delivered by amazon/i.test(full)) {
                        fulfilled = /amazon\\.ae/i.test(full) ? 'Amazon.ae' : amazonLabel();
                    } else if (ships && !isJunkName(ships)) {
                        fulfilled = ships;
                    }
                    // Amazon buy-box cards often have no Sold-by link — still keep owner
                    if (!seller) {
                        if (/sold by\\s+amazon/i.test(full) || /ships from and sold by amazon/i.test(full)) {
                            seller = /amazon\\.ae/i.test(full) ? 'Amazon.ae' : amazonLabel();
                        } else if (ships && /amazon/i.test(ships) && !/sold by\\s+(?!amazon)[\\w]/i.test(full)) {
                            seller = /amazon\\.ae/i.test(ships) ? 'Amazon.ae' : (clean(ships) || amazonLabel());
                        } else if (fulfilled && /amazon/i.test(fulfilled) && pinned) {
                            seller = fulfilled;
                        }
                    }
                    // Skip junk cards with no real merchant (e.g. only "Details")
                    if (!seller) return;
                    if (!price && !seller) return;
                    const key = (seller || '') + '|' + (price || '') + '|' + (ships || '');
                    if (seen.has(key)) return;
                    seen.add(key);
                    offers.push({
                        buybox_available: 'Yes',
                        buybox_price: price,
                        buybox_owner: seller,
                        buybox_ships_from: (ships && !isJunkName(ships)) ? ships : null,
                        buybox_fulfilled_by: fulfilled,
                        buybox_condition: 'New',
                        buybox_delivery: null,
                        is_buybox_winner: pinned ? 'Yes' : 'No',
                    });
                };

                pushOffer(document.querySelector('#aod-pinned-offer'), true);

                document.querySelectorAll(
                    '#aod-offer-list > div, #aod-offer, div.aod-offer'
                ).forEach((el) => {
                    if (el.id === 'aod-pinned-offer') return;
                    const t = text(el);
                    if (!el.querySelector('.a-price, .a-price-whole') && !/sold by/i.test(t)) return;
                    if (t.length > 1200) return;
                    pushOffer(el, false);
                });

                if (offers.filter(o => o.buybox_owner).length <= 1) {
                    document.querySelectorAll('#aod-container div, #all-offers-display-scroller div, #aod-offer-list div').forEach((el) => {
                        const t = text(el);
                        if (!/sold by/i.test(t)) return;
                        if (!el.querySelector('.a-price, .a-price-whole')) return;
                        if (t.length < 40 || t.length > 700) return;
                        pushOffer(el, false);
                    });
                }
                return offers;
            }"""
        )
    except Exception:
        raw_list = []

    out: list[dict[str, Any]] = []
    if not isinstance(raw_list, list):
        return out
    dom = domain or ""
    if not dom:
        try:
            dom = (page.url or "").split("/")[2]
        except Exception:
            dom = ""
    for raw in raw_list:
        if isinstance(raw, dict):
            raw["_domain"] = dom
            out.append(_finalize_offer(raw))
    return out


async def open_all_buying_options(page: Page, domain: str, asin: str) -> bool:
    """Open AOD / 'Other sellers on Amazon' so every seller offer can be scraped."""
    for sel in [
        "#buybox-see-all-buying-options-announce",
        "a[href*='aod_']",
        "span[data-action='show-all-offers-display'] a",
        "a:has-text('See All Buying Options')",
        "a:has-text('Other sellers')",
        "a:has-text('New (')",
        "#aod-ingress-link",
        "a#aod-ingress-link",
        "#olp-new a",
        "#olpLinkWidget_feature_div a",
        "a:has-text('New & Used')",
        "a[href*='/gp/offer-listing/']",
    ]:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=3000)
                await asyncio.sleep(1.0)
                try:
                    await page.wait_for_selector(
                        "#aod-offer, #aod-container, #aod-pinned-offer, #aod-offer-list, .aod-offer",
                        timeout=8000,
                    )
                    await asyncio.sleep(0.5)
                    return True
                except Exception:
                    pass
        except Exception:
            continue

    try:
        for url in (
            f"https://{domain}/gp/offer-listing/{asin}/ref=dp_olp_NEW_mbc?ie=UTF8&condition=new",
            f"https://{domain}/dp/{asin}?th=1&psc=1&aod=1",
        ):
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(0.8)
            await _close_popups(page)
            try:
                ingress = page.locator("#aod-ingress-link, a:has-text('Other sellers')").first
                if await ingress.count() and await ingress.is_visible():
                    await ingress.click(timeout=3000)
                    await asyncio.sleep(1.0)
            except Exception:
                pass
            html = await page.content()
            if "aod-offer" in html or "aod-offer-list" in html or "Sold by" in html:
                return True
    except Exception:
        return False
    return False


async def collect_all_offers(page: Page, domain: str, asin: str) -> list[dict[str, Any]]:
    """Primary buy box + all other seller offers. Empty → caller fills 'no buybox'."""
    # Let dynamic Sold by / merchant-info hydrate (parallel scrapes race this)
    try:
        await page.wait_for_selector(
            "#sellerProfileTriggerId, #merchant-info, #tabular-buybox, "
            "#merchantInfoFeature_feature_div, [offer-display-feature-name*='merchant'], "
            "#add-to-cart-button, #buybox",
            timeout=3500,
        )
    except Exception:
        pass
    try:
        await asyncio.sleep(0.45)
    except Exception:
        pass

    offers: list[dict[str, Any]] = []
    primary = await extract_buybox(page, domain)
    if primary.get("buybox_available") not in {"no buybox", "No", None} or primary.get(
        "buybox_price"
    ) or primary.get("buybox_owner"):
        if primary.get("buybox_available") == "No" and not primary.get("buybox_price"):
            pass
        else:
            if primary.get("buybox_available") == "No":
                primary["buybox_available"] = "Yes"
            primary["is_buybox_winner"] = "Yes"
            if primary.get("buybox_is_amazon") == "Yes" and not primary.get("buybox_owner"):
                primary["buybox_owner"] = _amazon_owner_label(domain)
            offers.append(primary)

    opened = await open_all_buying_options(page, domain, asin)
    pinned_owner: str | None = None
    pinned_ships: str | None = None
    pinned_price: str | None = None
    if opened:
        more = await extract_aod_offers(page, domain)
        seen: set[tuple[str, str]] = set()
        for o in offers:
            seen.add(
                (
                    str(o.get("buybox_owner") or "").lower(),
                    _norm_price_key(o.get("buybox_price")),
                )
            )
        for idx, o in enumerate(more):
            o["buybox_owner"] = _clean_seller_name(o.get("buybox_owner"))
            if o.get("buybox_is_amazon") == "Yes" and not o.get("buybox_owner"):
                o["buybox_owner"] = _amazon_owner_label(domain)
            # Skip empty junk offers
            if not o.get("buybox_price") and not o.get("buybox_owner"):
                continue
            # First AOD card / pinned often mirrors the buy-box winner
            if idx == 0 and o.get("buybox_owner"):
                pinned_owner = o.get("buybox_owner")
                pinned_ships = o.get("buybox_ships_from")
                pinned_price = o.get("buybox_price")
            seller = str(o.get("buybox_owner") or "").lower()
            price = _norm_price_key(o.get("buybox_price"))
            key = (seller, price)
            if seller and key in seen:
                continue
            if not seller and any(s == "" and p == price for s, p in seen):
                continue
            seen.add(key)
            o["is_buybox_winner"] = "No" if offers else (o.get("is_buybox_winner") or "No")
            offers.append(o)

    if offers and not any(str(o.get("is_buybox_winner")) == "Yes" for o in offers):
        offers[0]["is_buybox_winner"] = "Yes"

    # Repair blank winners: same-price AOD row → pinned AOD → ships-from Amazon
    for o in offers:
        o["buybox_owner"] = _clean_seller_name(o.get("buybox_owner"))
        o["buybox_ships_from"] = _clean_seller_name(o.get("buybox_ships_from"))
    for o in offers:
        if str(o.get("is_buybox_winner")) != "Yes":
            continue
        if o.get("buybox_owner"):
            continue
        price = _norm_price_key(o.get("buybox_price"))
        if price:
            for other in offers:
                if other is o:
                    continue
                if _norm_price_key(other.get("buybox_price")) == price and other.get("buybox_owner"):
                    o["buybox_owner"] = other["buybox_owner"]
                    o["buybox_ships_from"] = o.get("buybox_ships_from") or other.get(
                        "buybox_ships_from"
                    )
                    o["buybox_fulfilled_by"] = o.get("buybox_fulfilled_by") or other.get(
                        "buybox_fulfilled_by"
                    )
                    break
        if not o.get("buybox_owner") and pinned_owner:
            # Use pinned when price matches or winner price missing
            if (not price) or (not pinned_price) or _norm_price_key(pinned_price) == price:
                o["buybox_owner"] = pinned_owner
                o["buybox_ships_from"] = o.get("buybox_ships_from") or pinned_ships
        if not o.get("buybox_owner") and _is_amazon_seller(o.get("buybox_ships_from")):
            o["buybox_owner"] = _amazon_owner_label(domain, o.get("buybox_ships_from"))
        if not o.get("buybox_owner") and _is_amazon_seller(o.get("buybox_fulfilled_by")):
            o["buybox_owner"] = _amazon_owner_label(domain, o.get("buybox_fulfilled_by"))

    # Drop totally empty no-buybox noise rows (keep one if that's all we have)
    cleaned: list[dict[str, Any]] = []
    for o in offers:
        if (
            o.get("buybox_available") == "no buybox"
            and not o.get("buybox_price")
            and not o.get("buybox_owner")
            and str(o.get("is_buybox_winner")) != "Yes"
        ):
            continue
        cleaned.append(o)
    if cleaned:
        offers = cleaned

    named = sum(1 for o in offers if o.get("buybox_owner"))
    print(f"[BUYBOX] offers={len(offers)} with_seller_name={named}")
    return offers


def is_asin_not_found(
    html: str,
    page_title: str = "",
    page_url: str = "",
    http_status: int | None = None,
) -> bool:
    """True when Amazon shows the classic missing-product / 404 dog page."""
    if http_status == 404:
        return True
    title = (page_title or "").lower()
    url = (page_url or "").lower()
    if "page not found" in title:
        return True
    if "sorry" in title and "find" in title:
        return True
    lower = (html or "").lower()
    markers = [
        "sorry we couldn't find that page",
        "sorry! we couldn't find that page",
        "we couldn't find that page",
        "looking for something?",
        "dogs of amazon",
        "/gp/errors/404",
        "page-not-found",
        "the web address you entered is not a functioning page",
    ]
    if any(m in lower for m in markers):
        return True
    if "dp/" in url and "producttitle" not in lower and "couldn't find that page" in lower:
        return True
    return False


# ---------------------------------------------------------------------------
# Challenge / captcha
# ---------------------------------------------------------------------------


def is_challenge_html(html: str, page_title: str = "") -> bool:
    title = (page_title or "").lower()
    if "robot check" in title or "amazon.com/errors" in title:
        return True
    if "sorry! something went wrong" in title and "amazon" in title:
        return True
    lower = html.lower()
    markers = [
        "enter the characters you see below",
        "type the characters you see in this image",
        "api-services-support@amazon.com",
        "validatecaptcha",
        "/errors/validatecaptcha",
        "opfcaptcha.amazon",
        "automated access to amazon",
    ]
    return any(m in lower for m in markers)


def is_wrong_marketplace(page_url: str, expected_domain: str) -> str | None:
    """Detect redirect to another Amazon TLD (common from India → amazon.in)."""
    u = (page_url or "").lower()
    expected = expected_domain.lower().replace("www.", "")
    m = re.search(r"https?://(?:www\.)?(amazon\.[a-z0-9.]+)", u)
    if not m:
        return None
    landed = m.group(1).replace("www.", "")
    if landed == expected:
        return None
    return landed


async def wait_out_challenge(page: Page, seconds: int) -> bool:
    if seconds <= 0:
        return False
    deadline = time.time() + seconds
    while time.time() < deadline:
        html = await page.content()
        title = await page.title()
        if not is_challenge_html(html, title):
            return True
        await asyncio.sleep(2.0)
    return False


async def auto_warmup(page: Page, domain: str, marketplace: str) -> bool:
    """Silent headless warmup — no browser UI, no ENTER prompt."""
    home = f"https://{domain}/"
    print(f"[WARMUP] Headless check → {home}")
    try:
        await page.goto(home, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1.5)
        await _close_popups(page)
    except Exception as e:
        print(f"[WARMUP] Could not open Amazon: {e}")
        return False

    html = await page.content()
    title = await page.title()
    wrong = is_wrong_marketplace(page.url, domain)
    if wrong:
        print(f"[WARMUP] Redirected to {wrong} (wanted {domain}).")
        print("[WARMUP] Your IP looks Indian / wrong geo. Set a matching residential proxy.")
        return False
    if is_challenge_html(html, title):
        print("[WARMUP] Captcha / robot check on homepage.")
        print("[WARMUP] Stopping early to protect your IP. Fix proxy / wait and retry later.")
        return False

    print(f"[WARMUP] OK — Amazon {marketplace.upper()} reachable headlessly.\n")
    return True


async def manual_warmup(page: Page, domain: str, marketplace: str) -> bool:
    if not config.MANUAL_WARMUP:
        return await auto_warmup(page, domain, marketplace)

    home = f"https://{domain}/"
    require_enter = bool(getattr(config, "WARMUP_REQUIRE_ENTER", False))
    wait_s = int(getattr(config, "CAPTCHA_WAIT_SECONDS", 120) or 120)

    print("\n" + "=" * 62)
    if config.CONNECT_EXISTING_CHROME:
        hint = chrome_start_script()
        print(f"WARMUP: Use Chrome opened by {hint} (NOT Playwright Chrome).")
        if sys.platform == "win32":
            print(f"  1) Double-click {hint} (if Chrome not already open)")
        else:
            print(f"  1) Run ./{Path(hint).name} if Chrome not already open")
        print(f"  2) In that Chrome, open {home} if needed")
        print("  3) Complete captcha only if Amazon shows it")
        if require_enter:
            print("  4) Come back here and press ENTER")
        else:
            print("  4) No ENTER needed — script auto-starts when page is clear")
    else:
        print(f"WARMUP: Complete Amazon ({marketplace.upper()}) captcha if shown.")
        if require_enter:
            print("Then press ENTER.")
        else:
            print("No ENTER needed — auto-continues when clear.")
    print("=" * 62)

    try:
        await page.goto(home, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"[WARMUP] Could not open Amazon: {e}")

    async def _page_ready() -> bool:
        html = await page.content()
        title = await page.title()
        return not is_challenge_html(html, title)

    if await _page_ready():
        print(f"[WARMUP] OK — Amazon {marketplace.upper()} already clear. Starting scrape...\n")
        return True

    if require_enter:
        await asyncio.to_thread(
            input,
            f"\nPress ENTER when {marketplace.upper()} Amazon homepage is open (no captcha)... ",
        )
    else:
        print(
            f"[WARMUP] Captcha / challenge detected — solve it in Chrome. "
            f"Waiting up to {wait_s}s (no Enter needed)..."
        )
        deadline = time.time() + wait_s
        while time.time() < deadline:
            await asyncio.sleep(2.0)
            if await _page_ready():
                print(
                    f"[WARMUP] OK — Amazon {marketplace.upper()} session looks good. "
                    "Starting scrape...\n"
                )
                return True
        print("[WARMUP] Still on captcha after wait.")
        print(f"[WARMUP] Solve captcha in Chrome, then re-run — or set WARMUP_REQUIRE_ENTER = True.")
        return False

    if not await _page_ready():
        print("[WARMUP] Still on captcha / robot check. Captcha did NOT pass.")
        print(f"[WARMUP] Use {chrome_start_script()}, pass captcha, then run again.")
        return False

    print(f"[WARMUP] OK — Amazon {marketplace.upper()} session looks good. Starting scrape...\n")
    return True


# ---------------------------------------------------------------------------
# DOM helpers / extraction
# ---------------------------------------------------------------------------


async def _close_popups(page: Page) -> None:
    for sel in [
        'input[name="accept"]',
        '#sp-cc-accept',
        'button:has-text("Accept")',
        'button:has-text("Accept Cookies")',
        'button:has-text("Continue shopping")',
        'input[data-action-type="DISMISS"]',
        '#nav-main #nav-flyout-aya span.close',
        'button[aria-label="Close"]',
        'button:has-text("Dismiss")',
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.count() and await btn.is_visible():
                await btn.click(timeout=1500)
                await asyncio.sleep(0.3)
        except Exception:
            pass


async def _text(page: Page, selectors: list[str], timeout: int = 2500) -> str | None:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                t = (await loc.inner_text(timeout=timeout)).strip()
                if t:
                    return re.sub(r"\s+", " ", t)
        except Exception:
            continue
    return None


async def _attr(page: Page, selectors: list[str], attr: str) -> str | None:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                v = await loc.get_attribute(attr)
                if v and v.strip():
                    return v.strip()
        except Exception:
            continue
    return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).strip()
    return text or None


def _parse_json_ld(html: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.I | re.S,
    ):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("@type")
            types = t if isinstance(t, list) else [t]
            if not any(str(x).lower() == "product" for x in types if x):
                continue
            out["title"] = out.get("title") or _as_text(item.get("name"))
            brand = item.get("brand")
            if isinstance(brand, dict):
                out["brand"] = out.get("brand") or _as_text(brand.get("name"))
            else:
                out["brand"] = out.get("brand") or _as_text(brand)
            out["description"] = out.get("description") or _as_text(item.get("description"))
            img = item.get("image")
            if isinstance(img, list) and img:
                out["image_url"] = out.get("image_url") or _as_text(img[0])
            else:
                out["image_url"] = out.get("image_url") or _as_text(img)
            offers = item.get("offers")
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                out["price"] = out.get("price") or _clean_price(_as_text(offers.get("price")))
                out["currency"] = out.get("currency") or _as_text(offers.get("priceCurrency"))
                out["availability"] = out.get("availability") or _as_text(
                    str(offers.get("availability", "")).split("/")[-1]
                )
            agg = item.get("aggregateRating")
            if isinstance(agg, dict):
                out["rating"] = out.get("rating") or _as_text(agg.get("ratingValue"))
                out["reviews_count"] = out.get("reviews_count") or _as_text(
                    agg.get("reviewCount") or agg.get("ratingCount")
                )
    return out


def _clean_price(text: str | None) -> str | None:
    """Normalize Amazon price text to a plain number string like 9.52."""
    if not text:
        return None
    t = re.sub(r"\s+", " ", str(text)).strip()
    if re.search(r"price\s+not\s+available", t, re.I):
        return "price not available"
    if re.search(r"price\s+higher\s+than\s+typical", t, re.I):
        return None
    # Keep currency symbol optional in output — strip for parsing
    m = re.search(
        r"(?:USD|CAD|GBP|EUR|AED|\$|£|€)\s*([\d.,]+)|([\d.,]+)\s*(?:USD|CAD|GBP|EUR|AED)|([\d.,]+)",
        t,
        re.I,
    )
    if not m:
        return None
    raw = next(g for g in m.groups() if g)
    raw = raw.strip()

    # Both separators: decide decimal by last one
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            # 1.234,56 (EU)
            raw = raw.replace(".", "").replace(",", ".")
        else:
            # 1,234.56 (US)
            raw = raw.replace(",", "")
    elif "," in raw:
        parts = raw.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            # 1,14 → 1.14
            raw = parts[0] + "." + parts[1]
        else:
            raw = raw.replace(",", "")

    # Guard: "9 52" style from whole+fraction without separator
    if " " in raw:
        bits = raw.split()
        if len(bits) == 2 and bits[0].isdigit() and bits[1].isdigit():
            raw = f"{bits[0]}.{bits[1]}"

    if not re.search(r"\d", raw):
        return None
    return raw


PRICE_NOT_AVAILABLE = "price not available"


async def _buybox_price_suppressed(page: Page) -> bool:
    """
    True when Amazon hides the buy-box price with
    'Price higher than typical' / only 'See All Buying Options' (no Add to Cart).
    """
    try:
        return bool(
            await page.evaluate(
                """() => {
                    const body = (document.body && document.body.innerText) || '';
                    const buybox = document.querySelector(
                        '#desktop_buybox, #buybox, #rightCol, #ppd, #apex_desktop, #corePrice_feature_div, #corePriceDisplay_desktop_feature_div'
                    );
                    const zone = ((buybox && buybox.innerText) || body).slice(0, 8000);
                    if (/price\\s+higher\\s+than\\s+typical/i.test(zone)) return true;
                    if (/we have recently seen better prices/i.test(zone)) return true;
                    const addBtn =
                        document.querySelector('#add-to-cart-button') ||
                        document.querySelector('#buy-now-button') ||
                        document.querySelector('input#add-to-cart-button') ||
                        document.querySelector('input[name="submit.add-to-cart"]');
                    const seeAll =
                        document.querySelector('#buybox-see-all-buying-options-announce') ||
                        Array.from(document.querySelectorAll('a, span, input, button')).some(
                            (el) => /see\\s+all\\s+buying\\s+options/i.test((el.innerText || el.value || '').trim())
                        );
                    // Suppressed buy box: no ATC, but See All Buying Options is the CTA
                    if (!addBtn && seeAll && !document.querySelector('#price_inside_buybox .a-offscreen, #corePrice_feature_div .a-price:not(.a-text-price) .a-offscreen')) {
                        return true;
                    }
                    return false;
                }"""
            )
        )
    except Exception:
        return False


async def _extract_main_price(page: Page) -> str | None:
    """Read buy-box price. Never invent price from carousels when buy box is suppressed."""
    if await _buybox_price_suppressed(page):
        return PRICE_NOT_AVAILABLE
    try:
        raw = await page.evaluate(
            """() => {
                const zoneText = () => {
                    const buybox = document.querySelector(
                        '#desktop_buybox, #buybox, #rightCol, #apex_desktop, #corePrice_feature_div, #corePriceDisplay_desktop_feature_div'
                    );
                    return ((buybox && buybox.innerText) || '').slice(0, 5000);
                };
                if (/price\\s+higher\\s+than\\s+typical/i.test(zoneText())) {
                    return '__PRICE_SUPPRESSED__';
                }
                const roots = [
                    '#price_inside_buybox',
                    '#corePrice_feature_div',
                    '#corePriceDisplay_desktop_feature_div',
                    '#apex_desktop',
                    '#tp_price_block_total_price_ww',
                    '#price',
                ];
                for (const sel of roots) {
                    const box = document.querySelector(sel);
                    if (!box) continue;
                    // Skip if this box is the "higher than typical" message area
                    const bt = (box.innerText || '');
                    if (/price\\s+higher\\s+than\\s+typical/i.test(bt)) continue;
                    const off = box.querySelector('.a-price:not(.a-text-price) span.a-offscreen');
                    if (off && off.textContent && off.textContent.trim()) {
                        return off.textContent.trim();
                    }
                    const whole = box.querySelector('.a-price:not(.a-text-price) .a-price-whole');
                    const frac = box.querySelector('.a-price:not(.a-text-price) .a-price-fraction');
                    if (whole) {
                        const w = (whole.textContent || '').replace(/[^0-9]/g, '');
                        const f = frac
                            ? (frac.textContent || '').replace(/[^0-9]/g, '')
                            : '00';
                        if (w) return w + '.' + (f || '00');
                    }
                }
                // Do NOT fall back to random page prices (carousels / "options from AED …")
                return null;
            }"""
        )
        if raw == "__PRICE_SUPPRESSED__":
            return PRICE_NOT_AVAILABLE
        return _clean_price(raw)
    except Exception:
        return None


async def _extract_was_price(page: Page) -> str | None:
    if await _buybox_price_suppressed(page):
        return None
    try:
        raw = await page.evaluate(
            """() => {
                const roots = [
                    '#corePriceDisplay_desktop_feature_div',
                    '#corePrice_feature_div',
                    '#apex_desktop',
                ];
                for (const sel of roots) {
                    const box = document.querySelector(sel);
                    if (!box) continue;
                    if (/price\\s+higher\\s+than\\s+typical/i.test(box.innerText || '')) continue;
                    const off = box.querySelector(
                        'span.a-price.a-text-price[data-a-strike="true"] span.a-offscreen, span.a-price.a-text-price span.a-offscreen'
                    );
                    if (off && off.textContent && off.textContent.trim()) {
                        return off.textContent.trim();
                    }
                }
                const lp = document.querySelector('#listPrice, #priceblock_listprice');
                return lp && lp.textContent ? lp.textContent.trim() : null;
            }"""
        )
        return _clean_price(raw)
    except Exception:
        return None


async def set_delivery_postal(page: Page, marketplace: str, domain: str) -> bool:
    """
    Set Amazon delivery ZIP/postal so prices show for that country.
    From India, without this many US items say 'cannot be shipped' and hide price.
    """
    postal_map = getattr(config, "DELIVERY_POSTAL", {}) or {}
    postal = postal_map.get(marketplace) or postal_map.get(marketplace.lower())
    if not postal:
        print("[LOC] No DELIVERY_POSTAL configured — skipping location set.")
        return False

    print(f"[LOC] Setting delivery location to {postal!r} on {domain} ...")
    try:
        await page.goto(f"https://{domain}/", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1.0)
        await _close_popups(page)

        # Open location popover
        opened = False
        for sel in [
            "#nav-global-location-popover-link",
            "#glow-ingress-block",
            "#nav-global-location-slot",
            "#glow-ingress-line2",
        ]:
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=4000)
                    opened = True
                    await asyncio.sleep(1.2)
                    break
            except Exception:
                continue

        if not opened:
            print("[LOC] Could not open delivery location UI.")
            return False

        # Fill ZIP / postal
        filled = False
        for sel in [
            "#GLUXZipUpdateInput",
            "input[name='glowZipCode']",
            "input[data-action='GLUXPostalInputAction']",
            "#GLUXZipInput",
            "input[aria-label*='zip' i]",
            "input[aria-label*='postal' i]",
        ]:
            try:
                inp = page.locator(sel).first
                if await inp.count() and await inp.is_visible():
                    await inp.click(timeout=2000)
                    await inp.fill("")
                    await inp.fill(str(postal))
                    filled = True
                    await asyncio.sleep(0.4)
                    break
            except Exception:
                continue

        if not filled:
            print("[LOC] ZIP/postal input not found (layout may differ).")
            return False

        # Apply
        applied = False
        for sel in [
            "#GLUXZipUpdate",
            "input[aria-labelledby='GLUXZipUpdate-announce']",
            "span#GLUXZipUpdate span.a-button-text",
            "input[type='submit'][aria-labelledby*='GLUXZipUpdate']",
            "button:has-text('Apply')",
            "span.a-button-text:has-text('Apply')",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=3000)
                    applied = True
                    await asyncio.sleep(1.5)
                    break
            except Exception:
                continue

        # Confirm / Done if a second dialog appears
        for sel in [
            "#GLUXConfirmClose",
            "button[name='glowDoneButton']",
            "button:has-text('Done')",
            "button:has-text('Continue')",
            ".a-popover-footer button",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=2000)
                    await asyncio.sleep(0.8)
                    break
            except Exception:
                pass

        # Change zip via Enter if Apply didn't click
        if not applied:
            try:
                await page.keyboard.press("Enter")
                await asyncio.sleep(1.5)
            except Exception:
                pass

        print(f"[LOC] Delivery location set attempt finished ({postal}).")
        return True
    except Exception as e:
        print(f"[LOC] Failed to set location: {e}")
        return False


async def expand_product_information_sections(page: Page) -> None:
    """Open Product information / detail accordions so all tables are in the DOM."""
    for sel in [
        "#productDetails_db_sections",
        "#detailBulletsWrapper_feature_div",
        "#productDetails_feature_div",
        "a:has-text('See more product details')",
        "a:has-text('See all details')",
        "span:has-text('Product information')",
        "#nic-po-expander-heading",
        "[data-action='a-expander-toggle']",
    ]:
        try:
            locs = page.locator(sel)
            n = min(await locs.count(), 8)
            for i in range(n):
                el = locs.nth(i)
                if await el.is_visible():
                    await el.click(timeout=1500)
                    await asyncio.sleep(0.2)
        except Exception:
            continue

    # Click section headers like Item details / Style / Materials
    for label in (
        "Item details",
        "Style",
        "Materials & Care",
        "Measurements",
        "Features & Specs",
        "Product details",
        "Technical Details",
        "Additional Information",
    ):
        try:
            hdr = page.get_by_role("button", name=re.compile(label, re.I)).first
            if await hdr.count() and await hdr.is_visible():
                await hdr.click(timeout=1500)
                await asyncio.sleep(0.2)
        except Exception:
            pass
        try:
            hdr2 = page.locator(f"text={label}").first
            if await hdr2.count() and await hdr2.is_visible():
                await hdr2.click(timeout=1500)
                await asyncio.sleep(0.15)
        except Exception:
            pass


def _map_product_detail_label(label: str) -> str | None:
    key = re.sub(r"\s+", " ", (label or "").strip().lower())
    key = key.rstrip(":")
    return PRODUCT_DETAIL_LABEL_MAP.get(key)


async def extract_product_information(page: Page) -> dict[str, Any]:
    """
    Scrape Amazon 'Product information' tables (Item details, Style,
    Materials & Care, Measurements, Features & Specs) plus classic detail bullets.
    """
    await expand_product_information_sections(page)

    try:
        raw_pairs = await page.evaluate(
            """() => {
                const pairs = [];
                const push = (k, v) => {
                    if (!k || !v) return;
                    const key = String(k).replace(/\\s+/g, ' ').trim().replace(/:$/, '');
                    const val = String(v).replace(/\\s+/g, ' ').trim();
                    if (!key || !val) return;
                    if (key.length > 120) return;
                    pairs.push([key, val]);
                };

                // Newer Product information layout (expando / po tables)
                document.querySelectorAll(
                    '#productDetails_techSpec_section_1 tr, #productDetails_techSpec_section_2 tr, #productDetails_detailBullets_sections1 tr, #prodDetails tr, table.a-keyvalue tr, .prodDetTable tr, #productDetails_db_sections tr'
                ).forEach((tr) => {
                    const th = tr.querySelector('th, td.prodDetSectionEntry, .a-span3');
                    const td = tr.querySelector('td.prodDetAttrValue, td:not(.prodDetSectionEntry), .a-span9');
                    if (th && td) push(th.innerText, td.innerText);
                });

                // Detail bullets: "Label: Value"
                document.querySelectorAll(
                    '#detailBullets_feature_div li, #detailBulletsWrapper_feature_div li, #productDetails_detailBullets_sections1 li'
                ).forEach((li) => {
                    const t = (li.innerText || '').replace(/\\s+/g, ' ').trim();
                    const m = t.match(/^([^:]+):\\s*(.+)$/);
                    if (m) push(m[1], m[2]);
                });

                // Attribute expander / "Item details" style grids
                document.querySelectorAll(
                    '[data-cel-widget*="product-facts"] .a-spacing-small, .product-facts-detail, .a-section.a-spacing-small .a-fixed-left-grid'
                ).forEach((row) => {
                    const label = row.querySelector('.a-col-left, .a-text-bold, .rpi-attribute-label');
                    const value = row.querySelector('.a-col-right, .rpi-attribute-value, .a-color-base');
                    if (label && value) push(label.innerText, value.innerText);
                });

                // Generic fact rows in expandable product info
                document.querySelectorAll(
                    '#nic-po-expander-content tr, #poExpander tr, #productOverview_feature_div tr, #technicalSpecifications_feature_div tr'
                ).forEach((tr) => {
                    const cells = tr.querySelectorAll('td, th');
                    if (cells.length >= 2) push(cells[0].innerText, cells[1].innerText);
                });

                // po-attribute / overview attribute list
                document.querySelectorAll(
                    '#productOverview_feature_div .a-spacing-small, table.a-normal.a-spacing-micro tr'
                ).forEach((row) => {
                    const label = row.querySelector('td.a-span3, td:first-child');
                    const value = row.querySelector('td.a-span9, td:last-child');
                    if (label && value && label !== value) push(label.innerText, value.innerText);
                });

                return pairs;
            }"""
        )
    except Exception:
        raw_pairs = []

    out: dict[str, Any] = {}
    all_raw: list[str] = []
    if not isinstance(raw_pairs, list):
        return out

    for item in raw_pairs:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        label, value = str(item[0]), str(item[1])
        label = re.sub(r"\s+", " ", label).strip().rstrip(":")
        value = re.sub(r"\s+", " ", value).strip()
        if not label or not value:
            continue
        # Skip junk
        if label.lower() in {"customer reviews"} and "out of" in value.lower():
            # Prefer structured rating columns; still keep text in raw
            all_raw.append(f"{label}: {value}")
            m = re.search(r"([\d.]+)\s*out of\s*5", value, re.I)
            if m and not out.get("rating"):
                out["rating"] = m.group(1)
            m2 = re.search(r"([\d,]+)\s*rating", value, re.I)
            if m2 and not out.get("reviews_count"):
                out["reviews_count"] = m2.group(1).replace(",", "")
            continue

        all_raw.append(f"{label}: {value}")
        col = _map_product_detail_label(label)
        if not col:
            continue
        if col == "asin":
            # don't overwrite job asin unless empty — scrape_amazon sets it
            if not out.get("asin"):
                out["asin"] = value
            continue
        if col == "customer_reviews_text":
            continue
        if col not in out or not out[col]:
            out[col] = value

    if all_raw:
        # De-dupe while preserving order
        seen: set[str] = set()
        uniq: list[str] = []
        for line in all_raw:
            if line not in seen:
                seen.add(line)
                uniq.append(line)
        out["product_details_raw"] = " | ".join(uniq[:80])

    return out


async def extract_from_dom(page: Page) -> dict[str, Any]:
    data: dict[str, Any] = {}

    data["title"] = await _text(
        page,
        ["#productTitle", "#title", "span#title", "h1 span#productTitle"],
    )
    data["brand"] = await _text(
        page,
        [
            "#bylineInfo",
            "a#bylineInfo",
            "#brand",
            "tr.po-brand td.a-span9 span",
            "a#brand",
        ],
    )
    if data.get("brand"):
        data["brand"] = re.sub(
            r"^(Visit the|Brand:|Store:)\s*", "", data["brand"], flags=re.I
        ).strip()
        data["brand"] = re.sub(r"\s+Store$", "", data["brand"], flags=re.I).strip()

    data["price"] = await _extract_main_price(page)
    data["was_price"] = await _extract_was_price(page)
    price_suppressed = data.get("price") == PRICE_NOT_AVAILABLE

    data["availability"] = await _text(
        page,
        [
            "#availability span",
            "#availability",
            "#outOfStock",
            "#availability_feature_div",
        ],
    )

    rating = await _attr(page, ["span.a-icon-alt", "#acrPopover"], "title")
    if not rating:
        rating = await _text(page, ["span[data-hook='rating-out-of-text']", "#acrPopover span.a-size-base"])
    if rating:
        m = re.search(r"([\d.]+)\s*out of", rating, re.I)
        data["rating"] = m.group(1) if m else rating

    reviews = await _text(
        page,
        [
            "#acrCustomerReviewText",
            "span[data-hook='total-review-count']",
            "#averageCustomerReviews span#acrCustomerReviewText",
        ],
    )
    if reviews:
        m = re.search(r"([\d,]+)", reviews)
        data["reviews_count"] = m.group(1).replace(",", "") if m else reviews

    crumbs = await _text(
        page,
        ["#wayfinding-breadcrumbs_feature_div", "#wayfinding-breadcrumbs_container"],
    )
    data["category"] = crumbs

    # Bullet features
    try:
        bullets = page.locator("#feature-bullets ul li span.a-list-item")
        n = await bullets.count()
        feats: list[str] = []
        for i in range(min(n, 12)):
            t = (await bullets.nth(i).inner_text()).strip()
            t = re.sub(r"\s+", " ", t)
            if t and len(t) > 2:
                feats.append(t)
        if feats:
            data["features"] = " | ".join(feats)
    except Exception:
        pass

    data["description"] = await _text(
        page,
        [
            "#productDescription p",
            "#productDescription",
            "#aplus_feature_div",
            "#bookDescription_feature_div",
        ],
    )

    data["image_url"] = await _attr(
        page,
        ["#landingImage", "#imgTagWrapperId img", "#main-image", "img#imgBlkFront"],
        "src",
    )
    if not data.get("image_url"):
        data["image_url"] = await _attr(
            page,
            ["#landingImage", "#imgTagWrapperId img"],
            "data-old-hires",
        )

    data["seller"] = await _text(
        page,
        [
            "#sellerProfileTriggerId",
            "a#sellerProfileTriggerId",
            "#merchant-info",
            "div#merchant-info",
            "span.offer-display-feature-text",
        ],
    )
    data["ships_from"] = await _text(
        page,
        [
            "div[tabular-attribute-name='Ships from'] span",
            "#fulfillerInfoFeature_feature_div",
            "#mir-layout-DELIVERY_BLOCK",
        ],
    )

    # Primary buy box only here; full multi-offer list collected later
    try:
        host = (page.url or "").split("/")[2]
    except Exception:
        host = None
    buybox = await extract_buybox(page, host)
    data.update(buybox)
    if buybox.get("buybox_owner") and not data.get("seller"):
        data["seller"] = buybox["buybox_owner"]
    if buybox.get("buybox_ships_from") and not data.get("ships_from"):
        data["ships_from"] = buybox["buybox_ships_from"]
    # Suppressed buy box: never fill price from stray buybox/carousel numbers
    if price_suppressed or buybox.get("_price_suppressed"):
        data["price"] = PRICE_NOT_AVAILABLE
        data["was_price"] = None
        data["buybox_price"] = None
        data["_price_suppressed"] = True
        if data.get("buybox_available") in {None, "Yes", "No"}:
            data["buybox_available"] = "no buybox"
    elif buybox.get("buybox_price") and not data.get("price"):
        data["price"] = buybox["buybox_price"]

    # Full Product information tables (Item details / Style / Materials / etc.)
    details = await extract_product_information(page)
    for k, v in details.items():
        if v and (k not in data or not data.get(k)):
            data[k] = v
        elif k == "product_details_raw" and v:
            data[k] = v

    return {k: v for k, v in data.items() if v}


def empty_row(asin: str, marketplace: str, url: str) -> dict[str, Any]:
    row = {c: None for c in COLUMNS}
    row["asin"] = asin
    row["marketplace"] = marketplace
    row["url"] = url
    row["scraped_at"] = datetime.now(timezone.utc).isoformat()
    row["status"] = "Pending"
    return row


def _rows_from_offers(
    base: dict[str, Any], offers: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """One Excel row per offer. If none → single row with 'no buybox'."""
    if not offers:
        row = dict(base)
        row["offer_index"] = 1
        row["offer_count"] = 0
        row["is_buybox_winner"] = "No"
        row["buybox_available"] = "no buybox"
        row["buybox_price"] = None
        row["buybox_owner"] = None
        row["buybox_ships_from"] = None
        row["buybox_fulfilled_by"] = None
        row["buybox_is_amazon"] = None
        row["buybox_condition"] = None
        row["buybox_delivery"] = None
        return [row]

    rows: list[dict[str, Any]] = []
    count = len(offers)
    for i, offer in enumerate(offers, start=1):
        row = dict(base)
        row["offer_index"] = i
        row["offer_count"] = count
        row["is_buybox_winner"] = offer.get("is_buybox_winner") or ("Yes" if i == 1 else "No")
        for key in [
            "buybox_available",
            "buybox_price",
            "buybox_owner",
            "buybox_ships_from",
            "buybox_fulfilled_by",
            "buybox_is_amazon",
            "buybox_condition",
            "buybox_delivery",
        ]:
            if offer.get(key) is not None:
                row[key] = offer.get(key)
        if offer.get("buybox_owner"):
            row["seller"] = offer["buybox_owner"]
        if offer.get("buybox_ships_from"):
            row["ships_from"] = offer["buybox_ships_from"]
        # Main price column: keep "price not available" when Amazon suppressed the buy-box price.
        # Real offer amounts stay in buybox_price only.
        if base.get("price") == PRICE_NOT_AVAILABLE or base.get("_price_suppressed"):
            row["price"] = PRICE_NOT_AVAILABLE
        elif offer.get("buybox_price") and i == 1:
            row["price"] = offer["buybox_price"]
        rows.append(row)
    return rows


async def scrape_amazon_asin(
    page: Page,
    asin: str,
    marketplace: str,
    mcfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Scrape product + ALL buy box / seller offers. Returns one or more Excel rows."""
    url = product_url(mcfg["domain"], asin)
    base = empty_row(asin, marketplace, url)
    base["currency"] = mcfg.get("currency")

    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        base["http_status"] = resp.status if resp else None
        settle = float(getattr(config, "PAGE_SETTLE_SECONDS", 0.5) or 0.5)
        await asyncio.sleep(settle)
        await _close_popups(page)

        html = await page.content()
        title = await page.title()

        if is_asin_not_found(html, title, page.url, base.get("http_status")):
            base["status"] = "Skip"
            base["error"] = "ASIN not found on Amazon (404 / page missing) — skipped"
            base["title"] = None
            base["buybox_available"] = "no buybox"
            base["offer_index"] = 1
            base["offer_count"] = 0
            base["is_buybox_winner"] = "No"
            print(f"[SKIP] {asin} — page not found. Marked Skip, continuing next ASIN.")
            base["scraped_at"] = datetime.now(timezone.utc).isoformat()
            return [base]

        wrong = is_wrong_marketplace(page.url, mcfg["domain"])
        if wrong:
            base["status"] = "Blocked"
            base["error"] = f"Redirected to {wrong} (wanted {mcfg['domain']}) — wrong geo / need proxy"
            base["buybox_available"] = "no buybox"
            print(f"[GEO] {asin} redirected to {wrong}. Stopping retries for this ASIN.")
            return [base]

        if is_challenge_html(html, title):
            wait_s = int(getattr(config, "CAPTCHA_WAIT_SECONDS", 0) or 0)
            if wait_s > 0:
                print(f"[CAPTCHA] Robot check for {asin}. Waiting up to {wait_s}s...")
                ok = await wait_out_challenge(page, wait_s)
                if not ok:
                    base["status"] = "Blocked"
                    base["error"] = "Amazon captcha / robot check"
                    base["buybox_available"] = "no buybox"
                    return [base]
                html = await page.content()
                title = await page.title()
                if is_asin_not_found(html, title, page.url, base.get("http_status")):
                    base["status"] = "Skip"
                    base["error"] = "ASIN not found on Amazon (404 / page missing) — skipped"
                    base["buybox_available"] = "no buybox"
                    print(f"[SKIP] {asin} — page not found after captcha. Continuing.")
                    base["scraped_at"] = datetime.now(timezone.utc).isoformat()
                    return [base]
            else:
                print(f"[CAPTCHA] Robot check for {asin} — fail-fast (headless, no wait).")
                base["status"] = "Blocked"
                base["error"] = "Amazon captcha / robot check"
                base["buybox_available"] = "no buybox"
                return [base]

        # Soft wait for title (don't burn 12s if already there)
        try:
            await page.wait_for_selector("#productTitle, #title", timeout=5000)
        except Exception:
            pass

        html = await page.content()
        title = await page.title()

        if is_asin_not_found(html, title, page.url, base.get("http_status")):
            base["status"] = "Skip"
            base["error"] = "ASIN not found on Amazon (404 / page missing) — skipped"
            base["buybox_available"] = "no buybox"
            print(f"[SKIP] {asin} — page not found. Marked Skip, continuing next ASIN.")
            base["scraped_at"] = datetime.now(timezone.utc).isoformat()
            return [base]

        parsed = _parse_json_ld(html)
        # Product fields only (not multi-offer yet)
        dom = await extract_from_dom(page)
        # DOM wins for price (esp. "price not available" when buy box suppressed)
        parsed.update({k: v for k, v in dom.items() if v})
        if dom.get("price") == PRICE_NOT_AVAILABLE or dom.get("_price_suppressed"):
            parsed["price"] = PRICE_NOT_AVAILABLE
            parsed["was_price"] = None
            parsed["buybox_price"] = None
            parsed["_price_suppressed"] = True
            parsed["buybox_available"] = "no buybox"

        for key in [
            "title",
            "brand",
            "price",
            "was_price",
            "currency",
            "availability",
            "rating",
            "reviews_count",
            "category",
            "recommended_uses",
            "number_of_pieces",
            "included_components",
            "item_type_name",
            "reusability",
            "gtin",
            "manufacturer",
            "upc",
            "item_highlight",
            "unit_count",
            "model_number",
            "manufacturer_part_number",
            "best_sellers_rank",
            "color",
            "style_name",
            "shape",
            "pattern",
            "finish_types",
            "theme",
            "occasion_type",
            "seasons",
            "material_type",
            "product_care_instructions",
            "material_features",
            "item_dimensions",
            "size",
            "item_weight",
            "net_content_weight",
            "special_features",
            "product_details_raw",
            "features",
            "description",
            "image_url",
            "seller",
            "ships_from",
        ]:
            if parsed.get(key):
                base[key] = parsed[key]

        if not base.get("currency"):
            base["currency"] = mcfg.get("currency")

        lower_title = (base.get("title") or title or "").lower()
        if is_asin_not_found(html, lower_title, page.url, base.get("http_status")):
            base["status"] = "Skip"
            base["error"] = "ASIN not found on Amazon (404 / page missing) — skipped"
            base["title"] = None
            base["buybox_available"] = "no buybox"
            print(f"[SKIP] {asin} — page not found. Marked Skip, continuing next ASIN.")
            base["scraped_at"] = datetime.now(timezone.utc).isoformat()
            return [base]

        # Collect ALL offers from current product page (no second full reload)
        offers = await collect_all_offers(page, mcfg["domain"], asin)
        rows = _rows_from_offers(base, offers)

        if base.get("title"):
            for row in rows:
                row["status"] = "Success"
                row["error"] = None
                row["scraped_at"] = datetime.now(timezone.utc).isoformat()
            bb_note = rows[0].get("buybox_available")
            print(
                f"[OK] {asin} | offers={len(offers) or 0} | "
                f"buybox={bb_note} | {(base.get('title') or '')[:50]}"
            )
        else:
            for row in rows:
                row["status"] = "Failed"
                row["error"] = "Title not found (page may be blocked or layout changed)"
                row["buybox_available"] = row.get("buybox_available") or "no buybox"
                row["scraped_at"] = datetime.now(timezone.utc).isoformat()
            print(f"[FAIL] {asin} — no title (page title: {title[:80]!r})")

        return rows

    except Exception as e:
        base["status"] = "Error"
        base["error"] = str(e)[:300]
        base["buybox_available"] = "no buybox"
        base["offer_index"] = 1
        base["offer_count"] = 0
        base["scraped_at"] = datetime.now(timezone.utc).isoformat()
        print(f"[ERROR] {asin}: {e}")
        return [base]


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------


def chrome_start_script() -> str:
    """Platform-specific helper script name (amazon/)."""
    return "amazon/start_chrome.bat" if sys.platform == "win32" else "amazon/start_chrome.sh"


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


def debug_port_open(port: int | None = None) -> bool:
    p = int(port if port is not None else config.CHROME_DEBUG_PORT)
    try:
        with socket.create_connection(("127.0.0.1", p), timeout=1.5):
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
    """Mac/Windows: stop running Chrome so debug-port launch is not ignored."""
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


def _proxy_server_arg(proxy: dict[str, str] | None) -> str | None:
    """Chrome --proxy-server value (host:port). Auth proxies need Playwright launch instead."""
    if not proxy or not proxy.get("server"):
        return None
    server = proxy["server"]
    # strip scheme for Chrome flag where needed; Chrome accepts http://host:port
    if proxy.get("username"):
        # Embed user:pass — works on many Chrome builds for HTTP proxies
        m = re.match(r"^(https?|socks5)://(.+)$", server, re.I)
        if m:
            return f"{m.group(1)}://{proxy['username']}:{proxy.get('password') or ''}@{m.group(2)}"
    return server


def launch_debug_chrome(
    domain: str,
    *,
    port: int | None = None,
    profile_dir: str | Path | None = None,
    proxy: dict[str, str] | None = None,
    quit_existing: bool = True,
) -> None:
    chrome = find_chrome_executable()
    if not chrome:
        raise FileNotFoundError(
            "Google Chrome not found. Install Chrome or set Chrome path manually."
        )

    profile = Path(profile_dir or config.USER_DATA_DIR).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    if quit_existing:
        _quit_chrome_processes()
    _clear_chrome_profile_locks(profile)

    url = f"https://{domain}/"
    port_i = int(port if port is not None else config.CHROME_DEBUG_PORT)
    print(f"[BROWSER] Starting Chrome: {chrome.name} (port {port_i}) profile={profile.name}")

    extra: list[str] = []
    proxy_arg = _proxy_server_arg(proxy)
    if proxy_arg:
        extra.append(f"--proxy-server={proxy_arg}")
        print(f"[PROXY] Chrome --proxy-server={proxy.get('server')}")

    if sys.platform == "darwin":
        subprocess.Popen(
            [
                "open",
                "-na",
                "Google Chrome",
                "--args",
                f"--remote-debugging-port={port_i}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-session-crashed-bubble",
                *extra,
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return

    cmd = [
        str(chrome),
        f"--remote-debugging-port={port_i}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        *extra,
        url,
    ]
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


async def wait_for_debug_port(seconds: int = 45, port: int | None = None) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if debug_port_open(port):
            return True
        await asyncio.sleep(0.75)
    return False


async def connect_existing_chrome(
    p,
    domain: str,
    *,
    port: int | None = None,
    profile_dir: str | Path | None = None,
    proxy: dict[str, str] | None = None,
    quit_existing: bool = True,
) -> tuple[Browser, BrowserContext, Page, bool]:
    port_i = int(port if port is not None else config.CHROME_DEBUG_PORT)
    cdp_url = f"http://127.0.0.1:{port_i}"
    print(f"[BROWSER] Connecting to {cdp_url} ...")

    if not debug_port_open(port_i) and config.AUTO_LAUNCH_CHROME:
        print("[BROWSER] Chrome debug port not open — launching Chrome now...")
        try:
            launch_debug_chrome(
                domain,
                port=port_i,
                profile_dir=profile_dir,
                proxy=proxy,
                quit_existing=quit_existing,
            )
        except Exception as e:
            print(f"[BROWSER] Could not launch Chrome: {e}")
            print(f"[BROWSER] Or run {chrome_start_script()} manually.")
            raise SystemExit(1) from e
        print("[BROWSER] Waiting for Chrome to start...")
        if not await wait_for_debug_port(45, port=port_i):
            print("[BROWSER] Chrome did not open debug port in time.")
            raise SystemExit(1)

    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            try:
                browser = await p.chromium.connect_over_cdp(cdp_url, no_defaults=True)
            except TypeError:
                browser = await p.chromium.connect_over_cdp(cdp_url)
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
    print(f"[BROWSER] Fix:")
    print(f"  1) Quit ALL Chrome (Cmd+Q)")
    print(f"  2) Run:  ./{Path(chrome_start_script()).name}   (from amazon/)")
    print(f"  3) Wait for Amazon homepage — do not close Chrome")
    print(f"  4) python3 scraper.py us")
    if last_error and "setDownloadBehavior" in str(last_error):
        print("[BROWSER] Also upgrade Playwright:  pip3 install -U 'playwright>=1.60'")
    raise SystemExit(1) from last_error


async def create_context(
    p,
    mcfg: dict[str, Any],
    proxy: dict[str, str] | None = None,
    *,
    worker_id: int = 0,
) -> tuple[BrowserContext, Page, Browser | None, bool]:
    profile = Path(config.USER_DATA_DIR)
    # Separate profile per marketplace so cookies/locale don't bleed across geos
    marketplace_key = next(
        (k for k, v in config.MARKETPLACES.items() if v.get("domain") == mcfg.get("domain")),
        "default",
    )
    if worker_id:
        profile = profile / f"w{worker_id}" / marketplace_key
    else:
        profile = profile / marketplace_key
    profile.mkdir(parents=True, exist_ok=True)

    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
    ]
    if config.HEADLESS:
        args.append("--headless=new")
    if config.HIDE_BROWSER_WINDOW and not config.HEADLESS:
        args.extend(["--window-position=-32000,-32000", "--start-minimized"])

    ua = random.choice(config.USER_AGENTS)

    launch_kwargs: dict[str, Any] = {
        "user_data_dir": str(profile.resolve()),
        "headless": config.HEADLESS,
        "slow_mo": config.SLOW_MO_MS,
        "args": args,
        "ignore_default_args": ["--enable-automation"],
        "viewport": {"width": 1366, "height": 768},
        "locale": mcfg.get("locale", "en-US"),
        "timezone_id": mcfg.get("timezone_id", "America/New_York"),
        "user_agent": ua,
        "extra_http_headers": {
            "Accept-Language": mcfg.get("accept_language", "en-US,en;q=0.9"),
        },
    }
    if proxy:
        launch_kwargs["proxy"] = proxy
        print(f"[PROXY] Browser routed via {proxy.get('server')}")
    else:
        print("[WARN] No proxy attached to browser.")

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
    mode = "headless background" if config.HEADLESS else "visible"
    print(f"[BROWSER] Started Chrome ({mode}) worker={worker_id} — no Enter needed.")
    return context, page, None, False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


ALL_MARKETPLACES = tuple(config.MARKETPLACES.keys())


def resolve_marketplace_arg() -> str:
    if len(sys.argv) >= 2 and not sys.argv[1].startswith("-"):
        return sys.argv[1].strip().lower()
    for i, arg in enumerate(sys.argv):
        if arg in ("--marketplace", "--geo", "-m") and i + 1 < len(sys.argv):
            return sys.argv[i + 1].strip().lower()
    return str(config.MARKETPLACE).strip().lower()


def resolve_marketplaces() -> list[str]:
    arg = resolve_marketplace_arg()
    if arg in {"all", "*"}:
        # Every marketplace defined in config.MARKETPLACES
        return list(config.MARKETPLACES.keys())
    if arg not in config.MARKETPLACES:
        raise ValueError(
            f"Unknown marketplace '{arg}'. Use: {', '.join(config.MARKETPLACES)}, or all"
        )
    return [arg]


def make_shared_run_folder() -> Path:
    root = Path(getattr(config, "OUTPUT_DIR", "output"))
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if getattr(config, "OUTPUT_FOLDER_BY_DATETIME", True):
        folder = root / stamp
    else:
        folder = root
    folder.mkdir(parents=True, exist_ok=True)
    return folder


async def scrape_one_marketplace(
    page: Page,
    marketplace: str,
    run_folder: Path | None = None,
    *,
    asins_override: list[str] | None = None,
    out_path_override: Path | None = None,
    worker_id: int = 0,
    results_seed: list[dict[str, Any]] | None = None,
    done_seed: set[str] | None = None,
) -> Path:
    """Scrape one geo into its Excel. Returns output path."""
    mcfg = marketplace_cfg(marketplace)
    ensure_proxy_or_exit(marketplace, mcfg)

    try:
        asins = list(asins_override) if asins_override is not None else load_asins(mcfg["asin_file"])
    except Exception as e:
        print(f"[FATAL] Could not load ASINs for {marketplace}: {e}")
        raise

    if asins_override is None and config.MAX_ASINS is not None:
        asins = asins[: config.MAX_ASINS]

    worker_tag = f" W{worker_id}" if worker_id else ""
    print(f"\n{'=' * 62}")
    print(f"[INPUT] Marketplace: {marketplace.upper()} ({mcfg['name']}){worker_tag}")
    print(f"[INPUT] Domain: https://{mcfg['domain']}/")
    print(f"[INPUT] {len(asins)} ASINs")
    print(
        f"[SAFE] delay {config.DELAY_BETWEEN_ASINS_MIN}-{config.DELAY_BETWEEN_ASINS_MAX}s | "
        f"stop after {getattr(config, 'MAX_CONSECUTIVE_BLOCKS', 3)} blocks | "
        f"Enter={'ON' if getattr(config, 'WARMUP_REQUIRE_ENTER', False) else 'OFF (auto)'}"
    )

    if out_path_override is not None:
        out_path = Path(out_path_override)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        results = list(results_seed or [])
        done = set(done_seed or set())
        print(f"[OUTPUT] → {out_path}")
    elif getattr(config, "NEW_FILE_EACH_RUN", True):
        out_path = make_run_output_path(marketplace, run_folder=run_folder)
        results = list(results_seed or [])
        done = set(done_seed or set())
        print(f"[OUTPUT] → {out_path}")
    else:
        out_path = Path(mcfg["output_file"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        results = list(results_seed) if results_seed is not None else load_results(out_path)
        done = (
            set(done_seed)
            if done_seed is not None
            else (successful_asins(results) if config.RESUME_FROM_CHECKPOINT else set())
        )
        if getattr(config, "FORCE_RESCRAPE_ALL", False) and results_seed is None:
            done = set()
            results = []
        elif done:
            print(f"[RESUME] Skipping {len(done)} already Success/Skip ASINs.")

    consecutive_blocks = 0
    max_blocks = int(getattr(config, "MAX_CONSECUTIVE_BLOCKS", 3) or 3)

    if not await manual_warmup(page, mcfg["domain"], marketplace):
        print(f"[STOP] Warmup failed for {marketplace.upper()}{worker_tag} — skipping.")
        save_results(results, out_path)
        return out_path

    await set_delivery_postal(page, marketplace, mcfg["domain"])

    for i, asin in enumerate(asins, start=1):
        if asin in done:
            print(f"[SKIP] Already done: {asin}")
            continue

        print(f"\n----- [{marketplace.upper()}{worker_tag}] ASIN {i}/{len(asins)}: {asin} -----")
        rows: list[dict[str, Any]] = [
            empty_row(asin, marketplace, product_url(mcfg["domain"], asin))
        ]
        for attempt in range(1, config.MAX_RETRIES_PER_ASIN + 1):
            rows = await scrape_amazon_asin(page, asin, marketplace, mcfg)
            status = str((rows[0] if rows else {}).get("status", ""))
            if status == "Success":
                consecutive_blocks = 0
                break
            if status in {"Skip", "NotFound"}:
                break
            if attempt < config.MAX_RETRIES_PER_ASIN:
                print(
                    f"[RETRY] {asin} attempt {attempt + 1}/{config.MAX_RETRIES_PER_ASIN}"
                )
                await asyncio.sleep(random.uniform(1.0, 2.0))
                if status == "Blocked":
                    await asyncio.sleep(config.BLOCK_COOLDOWN_SECONDS)

        results = upsert_asin_rows(results, asin, rows)
        save_results(results, out_path)
        status = str((rows[0] if rows else {}).get("status", ""))
        if status == "Success":
            done.add(asin)
            consecutive_blocks = 0
        elif status in {"Skip", "NotFound"}:
            done.add(asin)
            consecutive_blocks = 0
            print("[SKIP] Saved in Excel as status=Skip → next ASIN")
        elif status == "Blocked":
            consecutive_blocks += 1
            print(f"[SAFE] Consecutive blocks: {consecutive_blocks}/{max_blocks}")
            if consecutive_blocks >= max_blocks:
                print(
                    f"\n[STOP] Too many blocks on {marketplace.upper()}{worker_tag}. "
                    "Stopping this worker to protect IP."
                )
                break

        if status in {"Skip", "NotFound"}:
            await asyncio.sleep(random.uniform(0.5, 1.0))
        else:
            await asyncio.sleep(
                random.uniform(
                    config.DELAY_BETWEEN_ASINS_MIN,
                    config.DELAY_BETWEEN_ASINS_MAX,
                )
            )

    print(f"\n[DONE] {marketplace.upper()}{worker_tag} saved -> {out_path.resolve()}")
    print(f"[DONE] {marketplace.upper()}{worker_tag} rows: {len(results)}")
    return out_path


def _merge_worker_excels(part_paths: list[Path], final_path: Path) -> Path:
    """Merge parallel worker Excel parts into one file."""
    by_asin: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for part in part_paths:
        rows = load_results(part)
        file_groups: dict[str, list[dict[str, Any]]] = {}
        file_order: list[str] = []
        for r in rows:
            asin = str(r.get("asin") or "").strip().upper()
            if not asin:
                continue
            if asin not in file_groups:
                file_groups[asin] = []
                file_order.append(asin)
            file_groups[asin].append(r)
        for asin in file_order:
            if asin not in by_asin:
                order.append(asin)
            by_asin[asin] = file_groups[asin]

    merged: list[dict[str, Any]] = []
    for asin in order:
        merged.extend(by_asin.get(asin) or [])
    save_results(merged, final_path)
    return final_path


def _parallel_worker_process(
    worker_id: int,
    marketplace: str,
    asins: list[str],
    run_folder_str: str | None,
    out_part_str: str,
) -> str:
    """Process entry: one Chrome + optional proxy, no Enter (auto warmup)."""
    os.chdir(_AMAZON_DIR)
    if str(_AMAZON_DIR) not in sys.path:
        sys.path.insert(0, str(_AMAZON_DIR))

    async def _run() -> str:
        mcfg = marketplace_cfg(marketplace)
        proxy = resolve_worker_proxy(marketplace, worker_id)
        port_base = int(getattr(config, "CHROME_DEBUG_PORT_BASE", 9223) or 9223)
        port = port_base + worker_id
        profile = Path(config.USER_DATA_DIR) / f"w{worker_id}" / marketplace
        out_part = Path(out_part_str)
        run_folder = Path(run_folder_str) if run_folder_str else None

        print(f"\n[PARALLEL] Worker {worker_id} starting | ASINs={len(asins)} | port={port}")
        if proxy:
            print(f"[PARALLEL] Worker {worker_id} proxy → {proxy.get('server')}")
        else:
            print(f"[PARALLEL] Worker {worker_id} NO proxy (same IP risk)")

        async with async_playwright() as p:
            cdp_browser = None
            context = None
            using_cdp = False
            if config.CONNECT_EXISTING_CHROME:
                cdp_browser, context, page, using_cdp = await connect_existing_chrome(
                    p,
                    mcfg["domain"],
                    port=port,
                    profile_dir=profile,
                    proxy=proxy,
                    quit_existing=(worker_id == 0),
                )
            else:
                context, page, _, using_cdp = await create_context(
                    p, mcfg, proxy=proxy, worker_id=worker_id
                )
            try:
                await scrape_one_marketplace(
                    page,
                    marketplace,
                    run_folder,
                    asins_override=asins,
                    out_path_override=out_part,
                    worker_id=worker_id,
                )
            finally:
                if using_cdp and cdp_browser:
                    await cdp_browser.close()
                elif context is not None and not using_cdp:
                    await context.close()
        return str(out_part.resolve())

    return asyncio.run(_run())


async def scrape_one_marketplace_parallel(
    marketplace: str,
    run_folder: Path | None = None,
) -> Path:
    """Split ASINs across workers (~ASINs_PER_WORKER each), merge Excel at the end."""
    mcfg = marketplace_cfg(marketplace)
    ensure_proxy_or_exit(marketplace, mcfg)

    asins = load_asins(mcfg["asin_file"])
    if config.MAX_ASINS is not None:
        asins = asins[: config.MAX_ASINS]

    if getattr(config, "NEW_FILE_EACH_RUN", True):
        final_path = make_run_output_path(marketplace, run_folder=run_folder)
        results: list[dict[str, Any]] = []
        done: set[str] = set()
    else:
        final_path = Path(mcfg["output_file"])
        results = load_results(final_path)
        done = successful_asins(results) if config.RESUME_FROM_CHECKPOINT else set()
        if getattr(config, "FORCE_RESCRAPE_ALL", False):
            done = set()
            results = []

    pending = [a for a in asins if a not in done]
    n = worker_count(len(pending))
    per = getattr(config, "ASINs_PER_WORKER", None) or 10
    try:
        per_i = max(1, int(per))
    except (TypeError, ValueError):
        per_i = 10

    print(
        f"\n[PARALLEL] {marketplace.upper()} | pending ASINs={len(pending)} | "
        f"~{per_i}/browser → workers={n} (max {getattr(config, 'MAX_WORKERS', 15)})"
    )
    print("[PARALLEL] No Enter needed — auto warmup (WARMUP_REQUIRE_ENTER=False)")
    if not pending:
        print(f"[PARALLEL] Nothing to scrape for {marketplace.upper()}.")
        save_results(results, final_path)
        return final_path

    if n <= 1 or len(pending) == 1:
        chunks = [pending]
    elif getattr(config, "ASINs_PER_WORKER", None) and getattr(config, "WORKERS", None) in (
        None,
        "",
    ):
        # Fixed 10 ASINs per browser, then cap to n workers by merging overflow
        chunks = split_asins_fixed_size(pending, per_i)
        if len(chunks) > n:
            # Merge overflow into last allowed workers (round-robin extras)
            extra = chunks[n:]
            chunks = chunks[:n]
            for i, ex in enumerate(extra):
                chunks[i % n].extend(ex)
    else:
        chunks = split_asins_round_robin(pending, n)

    jobs = [(i, chunk) for i, chunk in enumerate(chunks) if chunk]
    print(
        "[PARALLEL] Browser ASIN loads: "
        + ", ".join(f"W{i}={len(c)}" for i, c in jobs)
    )
    part_paths = [
        final_path.with_name(f"{final_path.stem}_w{wid}{final_path.suffix}")
        for wid, _ in jobs
    ]

    for pp in part_paths:
        save_results([], pp)

    run_folder_str = str(run_folder.resolve()) if run_folder else None

    results_parts: list[Path] = []
    with ProcessPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {}
        for (wid, chunk), pp in zip(jobs, part_paths):
            fut = pool.submit(
                _parallel_worker_process,
                wid,
                marketplace,
                chunk,
                run_folder_str,
                str(pp),
            )
            futures[fut] = (wid, pp)
            time.sleep(2.0)

        for fut in as_completed(futures):
            wid, pp = futures[fut]
            try:
                path_str = fut.result()
                results_parts.append(Path(path_str))
                print(f"[PARALLEL] Worker {wid} finished → {path_str}")
            except Exception as e:
                print(f"[PARALLEL] Worker {wid} FAILED: {e}")
                if pp.exists():
                    results_parts.append(pp)

    merge_inputs: list[Path] = []
    if results:
        seed = final_path.with_name(f"{final_path.stem}_seed{final_path.suffix}")
        save_results(results, seed)
        merge_inputs.append(seed)
    merge_inputs.extend(results_parts)
    _merge_worker_excels(merge_inputs, final_path)
    print(f"[PARALLEL] Merged → {final_path.resolve()}")
    return final_path


async def main() -> None:
    try:
        marketplaces = resolve_marketplaces()
    except ValueError as e:
        print(f"[FATAL] {e}")
        sys.exit(1)

    run_folder = (
        make_shared_run_folder()
        if getattr(config, "NEW_FILE_EACH_RUN", True)
        else None
    )
    if run_folder:
        print(f"[OUTPUT] Run folder → {run_folder.resolve()}")
    if len(marketplaces) > 1:
        print(f"[ALL] Will scrape in order: {', '.join(m.upper() for m in marketplaces)}")

    use_auto = bool(getattr(config, "ASINs_PER_WORKER", None)) and getattr(
        config, "WORKERS", None
    ) in (None, "")
    workers_hint = worker_count(150) if use_auto else worker_count()
    print(
        f"[CONFIG] ASINs_PER_WORKER={getattr(config, 'ASINs_PER_WORKER', None)} | "
        f"MAX_WORKERS={getattr(config, 'MAX_WORKERS', 15)} | "
        f"mode={'auto(~10/browser)' if use_auto else f'fixed({workers_hint})'} | "
        f"WARMUP_REQUIRE_ENTER={getattr(config, 'WARMUP_REQUIRE_ENTER', False)}"
    )

    first = marketplaces[0]
    first_cfg = marketplace_cfg(first)
    for m in marketplaces:
        ensure_proxy_or_exit(m, marketplace_cfg(m))

    saved: list[Path] = []

    # Auto parallel whenever ASINs_PER_WORKER is set (or fixed WORKERS > 1)
    use_parallel = use_auto or worker_count() > 1
    if use_parallel:
        print(
            "[PARALLEL] Auto browser count from ASINs (10 each). "
            "No Enter — captcha auto-wait if shown."
        )
        for idx, marketplace in enumerate(marketplaces, start=1):
            print(
                f"\n\n######## GEO {idx}/{len(marketplaces)}: "
                f"{marketplace.upper()} (parallel) ########"
            )
            try:
                out = await scrape_one_marketplace_parallel(marketplace, run_folder)
                saved.append(out)
            except Exception as e:
                print(f"[ERROR] {marketplace.upper()} parallel failed: {e}")
                continue
            if idx < len(marketplaces):
                await asyncio.sleep(random.uniform(2.0, 4.0))
    else:
        async with async_playwright() as p:
            cdp_browser: Browser | None = None
            using_cdp = False
            proxy = resolve_proxy(first)

            if config.CONNECT_EXISTING_CHROME:
                cdp_browser, context, page, using_cdp = await connect_existing_chrome(
                    p, first_cfg["domain"], proxy=proxy, quit_existing=True
                )
            else:
                context, page, _, using_cdp = await create_context(
                    p, first_cfg, proxy=proxy
                )

            try:
                for idx, marketplace in enumerate(marketplaces, start=1):
                    print(
                        f"\n\n######## GEO {idx}/{len(marketplaces)}: "
                        f"{marketplace.upper()} ########"
                    )
                    if idx > 1:
                        print(
                            f"[ALL] Switching to {marketplace.upper()}. "
                            "Captcha auto-wait if shown (no Enter)."
                        )
                    try:
                        out = await scrape_one_marketplace(page, marketplace, run_folder)
                        saved.append(out)
                    except Exception as e:
                        print(f"[ERROR] {marketplace.upper()} failed: {e}")
                        continue
                    if idx < len(marketplaces):
                        await asyncio.sleep(random.uniform(2.0, 4.0))
            finally:
                if using_cdp and cdp_browser:
                    await cdp_browser.close()
                elif not using_cdp:
                    await context.close()

    print("\n" + "=" * 62)
    print("[DONE] All requested marketplaces finished.")
    for path in saved:
        print(f"  → {path.resolve()}")
    print("=" * 62)


if __name__ == "__main__":
    try:
        from multiprocessing import freeze_support

        freeze_support()
    except Exception:
        pass
    asyncio.run(main())
