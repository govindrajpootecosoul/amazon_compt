"""
Extract Walmart 'How you'll get this item' tiles:
  Shipping  -> Arrives today / Order within ...
  Pickup    -> As soon as 7am today
  Delivery  -> As soon as 7am today
"""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page


STOCK_PHRASES = {
    "out of stock",
    "in stock",
    "not available",
    "available",
    "check nearby",
    "unavailable",
}


def humanize_stock(raw: str | None) -> str | None:
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
    }
    if key in mapping:
        return mapping[key]
    lower = text.lower()
    if "out of stock" in lower:
        return "Out of stock"
    if "check nearby" in lower:
        return "Check nearby"
    if "not available" in lower or "unavailable" in lower:
        return "Not available"
    if lower == "in stock":
        return "In stock"
    if lower == "available":
        return "Available"
    return text


def is_order_within(text: str) -> bool:
    return "order within" in (text or "").lower()


def is_eta(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    lower = t.lower()
    if lower in STOCK_PHRASES or is_order_within(lower):
        return False
    if re.search(
        r"arriv|as soon|today|tomorrow|get it|\bam\b|\bpm\b|\bby\b|\d{1,2}/\d{1,2}",
        lower,
        re.I,
    ):
        return True
    return False


def is_stock(text: str) -> bool:
    lower = (text or "").strip().lower()
    return lower in STOCK_PHRASES or humanize_stock(text) in {
        "Out of stock",
        "In stock",
        "Not available",
        "Available",
        "Check nearby",
    }


def parse_tile_lines(lines: list[str]) -> dict[str, str]:
    """
    From lines after the label (Shipping/Pickup/Delivery):
      ['Arrives today', 'Order within 10 hr 49 min']
      ['As soon as 7am today']
      ['Out of stock']
    """
    out: dict[str, str] = {}
    status = None
    eta = None
    order_within = None
    for line in lines:
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if is_order_within(line) and not order_within:
            order_within = line
        elif is_eta(line) and not eta:
            eta = line
        elif is_stock(line) and not status:
            status = humanize_stock(line)
        elif not status and not eta and not order_within:
            # unknown line — prefer as ETA if time-like else status
            if is_eta(line):
                eta = line
            else:
                status = humanize_stock(line) or line

    if eta and not status:
        status = "Available"
    if status:
        out["status"] = status
    if eta:
        out["eta"] = eta
    if order_within:
        out["order_within"] = order_within
    return out


async def extract_fulfillment_tiles(page: Page) -> dict[str, str]:
    """
    Primary source: visible fulfillment cards on the product page.
    Returns keys: shipping, shipping_eta, shipping_order_within, pickup, pickup_eta, delivery, delivery_eta
    """
    raw = await page.evaluate(
        """() => {
            const normalize = (s) => (s || '').replace(/\\s+/g, ' ').trim();
            const labels = ['Shipping', 'Pickup', 'Delivery'];
            const result = {};

            // 1) Prefer the "How you'll get this item" block
            let scope = document.body;
            const all = Array.from(document.querySelectorAll('div, section, form, main'));
            for (const el of all) {
                const t = normalize(el.innerText || '');
                if (!t) continue;
                if (t.includes("How you'll get this item") && t.length < 2500) {
                    // take the smallest reasonable container that still has all 3 labels
                    const hasAll = labels.every(l => t.includes(l));
                    if (hasAll) {
                        scope = el;
                        break;
                    }
                }
            }

            // 2) Collect candidate cards: buttons / radio tiles
            const candidates = Array.from(
                scope.querySelectorAll('button, [role="radio"], [role="button"], label, div')
            );

            for (const label of labels) {
                let best = null;
                let bestScore = 1e9;
                for (const el of candidates) {
                    const text = normalize(el.innerText || '');
                    if (!text || text.length > 240) continue;
                    const lines = (el.innerText || '').split('\\n').map(normalize).filter(Boolean);
                    if (!lines.length) continue;
                    const first = lines[0];
                    const starts =
                        first.toLowerCase() === label.toLowerCase() ||
                        text.toLowerCase().startsWith(label.toLowerCase() + ' ') ||
                        text.toLowerCase().startsWith(label.toLowerCase() + '\\n');
                    if (!starts) continue;
                    // Prefer compact tiles (real cards are short)
                    const score = text.length;
                    if (score < bestScore) {
                        bestScore = score;
                        best = lines;
                    }
                }
                if (!best) continue;

                // Drop leading label line
                let idx = best.findIndex(l => l.toLowerCase() === label.toLowerCase());
                let rest;
                if (idx >= 0) rest = best.slice(idx + 1);
                else {
                    // "Shipping Arrives today" single line split
                    const joined = best.join(' ');
                    rest = [joined.slice(label.length).trim()].filter(Boolean);
                }
                result[label] = rest;
            }
            return result;
        }"""
    )

    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out

    for label, lines in raw.items():
        if not isinstance(lines, list):
            continue
        parsed = parse_tile_lines([str(x) for x in lines])
        key = label.lower()
        if parsed.get("status"):
            out[key] = parsed["status"]
        if parsed.get("eta"):
            out[f"{key}_eta"] = parsed["eta"]
        if key == "shipping" and parsed.get("order_within"):
            out["shipping_order_within"] = parsed["order_within"]
    return out


async def click_fulfillment_and_reread(page: Page) -> dict[str, str]:
    """Click each tile (Shipping/Pickup/Delivery) then re-read — some ETAs hydrate on select."""
    combined: dict[str, str] = {}
    for label in ("Shipping", "Pickup", "Delivery"):
        try:
            loc = page.get_by_role("button", name=re.compile(rf"^{label}", re.I)).first
            if await loc.count() == 0:
                loc = page.locator(f"button:has-text('{label}')").first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=2500)
                await page.wait_for_timeout(800)
        except Exception:
            pass
        part = await extract_fulfillment_tiles(page)
        for k, v in part.items():
            if v and not combined.get(k):
                combined[k] = v
    # Final full read
    final = await extract_fulfillment_tiles(page)
    for k, v in final.items():
        if v:
            combined[k] = v
    return combined


def merge_fulfillment(base: dict[str, Any], extra: dict[str, str]) -> None:
    keys = (
        "shipping",
        "shipping_eta",
        "shipping_order_within",
        "pickup",
        "pickup_eta",
        "delivery",
        "delivery_eta",
    )
    for k in keys:
        if extra.get(k) and not base.get(k):
            base[k] = extra[k]


def fulfillment_looks_complete(data: dict[str, Any]) -> bool:
    """True when we have tile statuses and ETA where stock allows."""
    if not data.get("shipping") or not data.get("pickup") or not data.get("delivery"):
        return False
    # If shipping available / in stock, expect some ETA or order-within when Walmart shows it
    ship = str(data.get("shipping", "")).lower()
    if ship in {"available", "in stock"}:
        if data.get("shipping_eta") or data.get("shipping_order_within"):
            return True
        # still accept if delivery/pickup have etas
        if data.get("pickup_eta") or data.get("delivery_eta"):
            return True
        return False
    # Out of stock / not available — ETA often empty is OK
    return True
