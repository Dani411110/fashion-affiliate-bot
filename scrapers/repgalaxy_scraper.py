"""
RepGalaxy product scraper — scrapes repgalaxy.com/products/.

Each product card has: name, price, thumbnail image, CNFans link.
Full-size image is obtained by stripping the WordPress -WxH suffix from the thumbnail URL.
Products are stored in SQLite with source='repgalaxy'.

Run standalone: python -m scrapers.repgalaxy_scraper
"""

import asyncio
import re
import time
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright, Page
from playwright.async_api import TimeoutError as PWTimeout

from database.sqlite_db import get_db
from utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://repgalaxy.com/products/"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

CATEGORY_MAP: Dict[str, str] = {
    "hoodie": "tops",
    "t-shirt": "tops",
    "tshirt": "tops",
    "jacket": "tops",
    "shirt": "tops",
    "sweater": "tops",
    "pants": "bottoms",
    "jeans": "bottoms",
    "shorts": "bottoms",
    "shoes": "shoes",
    "sneaker": "shoes",
    "boots": "shoes",
    "bag": "bags",
    "wallet": "bags",
    "cap": "accessories",
    "hat": "accessories",
    "belt": "accessories",
    "watch": "accessories",
}


def _guess_category(name: str) -> str:
    name_lower = name.lower()
    for keyword, cat in CATEGORY_MAP.items():
        if keyword in name_lower:
            return cat
    return "tops"


def _full_size_url(thumbnail_url: str) -> str:
    """Remove WordPress -WxH dimension suffix to get full-size image."""
    return re.sub(r"-\d+x\d+(\.\w+)$", r"\1", thumbnail_url)


async def _load_all_products(page: Page) -> List[Dict[str, Any]]:
    """Click 'Load more' until all products are visible, then extract all cards."""
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    # Click "Load more" until it disappears
    for _ in range(20):
        try:
            btn = await page.query_selector("a:has-text('Load more'), button:has-text('Load more')")
            if not btn:
                break
            await btn.scroll_into_view_if_needed()
            await btn.click()
            await asyncio.sleep(2)
        except Exception:
            break

    # Extract product cards
    cards = await page.query_selector_all("article, .product, [class*='product-item'], [class*='product_item']")
    if not cards:
        # Fallback: any element with a price and image
        cards = await page.query_selector_all("div:has(img):has(a)")

    products = []
    seen_names = set()

    for card in cards:
        try:
            # Name
            name_el = await card.query_selector("h2, h3, h4, [class*='title'], [class*='name']")
            name = (await name_el.inner_text()).strip() if name_el else ""
            if not name or name in seen_names:
                continue

            # Price
            price_el = await card.query_selector("[class*='price'], .price, span:has-text('$')")
            price_text = (await price_el.inner_text()).strip() if price_el else "0"
            price_match = re.search(r"[\d.]+", price_text.replace(",", "."))
            price = float(price_match.group()) if price_match else 0.0
            if price <= 0:
                continue

            # Image (thumbnail → full size)
            img_el = await card.query_selector("img")
            img_src = ""
            if img_el:
                img_src = (await img_el.get_attribute("src") or
                           await img_el.get_attribute("data-src") or "").strip()
            if not img_src or "placeholder" in img_src:
                continue
            if img_src.startswith("//"):
                img_src = "https:" + img_src
            elif img_src.startswith("/"):
                img_src = "https://repgalaxy.com" + img_src
            full_img = _full_size_url(img_src)

            # CNFans link (preferred) or Kakobuy link
            cnfans_link = ""
            kakobuy_link = ""
            links = await card.query_selector_all("a")
            for link in links:
                href = (await link.get_attribute("href") or "").strip()
                text = (await link.inner_text()).strip().lower()
                if "cnfans" in href or "cnfans" in text:
                    cnfans_link = href
                elif "kakobuy" in href or "kakobuy" in text:
                    kakobuy_link = href

            affiliate_link = cnfans_link or kakobuy_link
            if not affiliate_link:
                # Use the card's own link
                card_link = await card.query_selector("a")
                if card_link:
                    affiliate_link = await card_link.get_attribute("href") or ""

            seen_names.add(name)
            products.append({
                "name": name,
                "price": price,
                "category": _guess_category(name),
                "image_url": full_img,
                "mulebuy_link": affiliate_link,
                "source": "repgalaxy",
            })
        except Exception as exc:
            logger.debug("Skipping card: {}", exc)
            continue

    return products


async def scrape_repgalaxy(max_products: int = 200) -> int:
    """Scrape RepGalaxy products and upsert into SQLite. Returns count of new products."""
    db = get_db()
    new_count = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=_USER_AGENT)
        page = await ctx.new_page()

        try:
            logger.info("Scraping RepGalaxy: {}", BASE_URL)
            products = await _load_all_products(page)
            logger.info("Found {} products on RepGalaxy", len(products))

            for product in products[:max_products]:
                try:
                    existing = db.get_product_by_name(product["name"])
                    if existing:
                        continue
                    db.add_product(
                        name=product["name"],
                        price=product["price"],
                        category=product["category"],
                        image_url=product["image_url"],
                        mulebuy_link=product["mulebuy_link"],
                        sheet_row_index=int(time.time() * 1000) % 999999,
                    )
                    new_count += 1
                    logger.debug("Added: {} (${:.2f})", product["name"], product["price"])
                except Exception as exc:
                    logger.warning("Failed to add product {}: {}", product.get("name"), exc)
        finally:
            await browser.close()

    logger.info("RepGalaxy scrape complete: {} new products added", new_count)
    return new_count


def run_scrape(max_products: int = 200) -> int:
    return asyncio.run(scrape_repgalaxy(max_products))


if __name__ == "__main__":
    count = run_scrape()
    print(f"Done. Added {count} new products from RepGalaxy.")
