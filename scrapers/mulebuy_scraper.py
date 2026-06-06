"""
Mulebuy.gg product scraper — replaces Google Sheets as product source.

Scrapes mulebuy.gg/products for each category and stores products in SQLite.
Each product: name, price, category, image_url, mulebuy_link.

Run standalone: python -m scrapers.mulebuy_scraper
Or called automatically by PostBuilder when product cache is stale.
"""

import asyncio
import random
import time
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright, BrowserContext, Page
from playwright.async_api import TimeoutError as PWTimeout

from database.sqlite_db import get_db
from utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL     = "https://mulebuy.gg/products"
MULEBUY_HOST = "https://mulebuy.gg"

CARD_SEL     = "div.group.relative.flex.flex-col.overflow-hidden.rounded-3xl.bg-white"
BUY_BTN_SEL  = "button:has-text('Buy Now with Mulebuy')"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# Category label on site → internal category name for our DB
CATEGORIES: Dict[str, str] = {
    "Jackets":        "tops",
    "T-shirts":       "tops",
    "Shoes":          "shoes",
    "Hoodies":        "tops",
    "Pants":          "bottoms",
    "Other Clothing": "tops",
    "Bags & Wallets": "bags",
    "Accessories":    "accessories",
}


async def _dismiss_popups(page: Page):
    """Close cookie banners or notification prompts."""
    for sel in ["button:has-text('Accept')", "button:has-text('Got it')", "[class*='cookie'] button"]:
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                await asyncio.sleep(0.4)
        except Exception:
            pass


async def _scroll_load(page: Page, scrolls: int = 6):
    last = 0
    for _ in range(scrolls):
        await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
        await asyncio.sleep(0.8)
        h = await page.evaluate("document.body.scrollHeight")
        if h == last:
            break
        last = h


async def _extract_card_info(card) -> Optional[Dict[str, Any]]:
    """Pull name / price / category / image from a card WITHOUT clicking."""
    try:
        img = await card.query_selector("img")
        name    = (await img.get_attribute("alt") or "").strip() if img else ""
        img_src = (await img.get_attribute("src") or "").strip() if img else ""
        if not name or not img_src:
            return None

        # Make absolute URL
        if img_src.startswith("/"):
            img_src = MULEBUY_HOST + img_src

        text = await card.inner_text()
        lines = [l.strip() for l in text.splitlines() if l.strip()]

        # Category is the ALL-CAPS line (e.g. "SHOES", "JACKETS")
        category_raw = next((l for l in lines if l.isupper() and len(l) > 2), "")

        # Price — first token starting with $
        price = 0.0
        for token in lines:
            if token.startswith("$"):
                try:
                    price = float(token.replace("$", "").replace(",", ""))
                    break
                except ValueError:
                    pass

        return {
            "name":         name,
            "img_src":      img_src,
            "category_raw": category_raw,
            "price":        price,
        }
    except Exception:
        return None


async def _get_buy_link(context: BrowserContext, page: Page, card) -> str:
    """Click card → modal → Buy Now → capture popup URL."""
    try:
        await card.scroll_into_view_if_needed()
        await card.click()
        await asyncio.sleep(1.2)

        # Wait for modal to appear
        await page.wait_for_selector(BUY_BTN_SEL, timeout=6_000)

        # Intercept popup
        async with context.expect_page(timeout=8_000) as popup_info:
            btn = await page.query_selector(BUY_BTN_SEL)
            await btn.click()

        popup = await popup_info.value
        await popup.wait_for_load_state("domcontentloaded", timeout=10_000)
        url = popup.url
        await popup.close()

        # Close modal
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.4)
        return url

    except PWTimeout:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
        return ""
    except Exception as exc:
        logger.debug("buy-link capture failed: {}", exc)
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return ""


async def _select_category(page: Page, label: str):
    """Click the category filter tab."""
    try:
        # Category tabs are buttons with text matching label
        tab = await page.wait_for_selector(
            f"button:has-text('{label}')", timeout=5_000
        )
        await tab.click()
        await asyncio.sleep(1.5)
    except PWTimeout:
        logger.warning("Category tab '{}' not found — using current view", label)


async def _scrape_category(
    context: BrowserContext,
    page: Page,
    category_label: str,
    internal_cat: str,
    target: int,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    await _select_category(page, category_label)
    await _scroll_load(page, scrolls=5)

    cards = await page.query_selector_all(CARD_SEL)
    logger.info("Category '{}': {} cards visible, target={}", category_label, len(cards), target)

    for card in cards:
        if len(results) >= target:
            break

        info = await _extract_card_info(card)
        if not info:
            continue

        await asyncio.sleep(random.uniform(0.3, 0.7))
        buy_link = await _get_buy_link(context, page, card)

        if not buy_link:
            # Fallback: search URL
            safe = info["name"].replace(" ", "+")
            buy_link = f"https://mulebuy.com/search/?keyword={safe}"

        # Unique key based on link + name
        row_index = abs(hash(buy_link + info["name"])) % (10 ** 9)

        results.append({
            "sheet_row_index":  row_index,
            "name":             info["name"],
            "image_url":        info["img_src"],
            "mulebuy_link":     buy_link,
            "category":         internal_cat,
            "price":            info["price"],
            "tags":             f"{category_label.lower()},mulebuy,fashion",
            "popularity_score": random.randint(60, 95),
        })

        logger.debug(
            "  Saved: {} — ${:.2f} → {}",
            info["name"][:35], info["price"], buy_link[:60]
        )

    return results


async def _scrape_async(
    categories: List[str],
    per_category: int,
) -> List[Dict[str, Any]]:
    all_products: List[Dict[str, Any]] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()

        try:
            logger.info("Opening mulebuy.gg/products …")
            await page.goto(BASE_URL, wait_until="networkidle", timeout=30_000)
            await asyncio.sleep(3)
            await _dismiss_popups(page)

            for label in categories:
                internal = CATEGORIES.get(label, "tops")
                products = await _scrape_category(
                    context, page, label, internal, per_category
                )
                all_products.extend(products)
                logger.info("  → {} products collected for '{}'", len(products), label)
                await asyncio.sleep(random.uniform(1.0, 2.0))

        finally:
            await browser.close()

    return all_products


def scrape_mulebuy(
    categories: Optional[List[str]] = None,
    per_category: int = 30,
    save_to_db: bool = True,
) -> List[Dict[str, Any]]:
    """
    Scrape mulebuy.gg and optionally save to SQLite products_cache.

    Args:
        categories:   list of category labels (keys of CATEGORIES). None = all.
        per_category: max products per category.
        save_to_db:   whether to upsert into SQLite.

    Returns:
        List of product dicts.
    """
    cats = categories or list(CATEGORIES.keys())
    logger.info(
        "Starting Mulebuy scrape — {} categories × {} each",
        len(cats), per_category
    )

    products = asyncio.run(_scrape_async(cats, per_category))
    logger.info("Mulebuy scrape done — {} total products", len(products))

    if save_to_db and products:
        get_db().sync_products(products)
        logger.info("Saved {} products to SQLite", len(products))

    return products


def get_cached_products(min_count: int = 50) -> List[Dict[str, Any]]:
    """Return products from SQLite. Triggers fresh scrape if cache is too small."""
    db = get_db()
    cached = db.get_all_cached_products()
    if len(cached) < min_count:
        logger.info(
            "Cache too small ({} < {}) — scraping Mulebuy now",
            len(cached), min_count
        )
        scrape_mulebuy()
        cached = db.get_all_cached_products()
    return cached


if __name__ == "__main__":
    import sys
    cats = sys.argv[1:] if len(sys.argv) > 1 else None
    prods = scrape_mulebuy(categories=cats, per_category=5)
    print(f"\nTotal: {len(prods)}")
    for p in prods[:5]:
        print(f"  {p['name']} | ${p['price']} | {p['mulebuy_link'][:70]}")
