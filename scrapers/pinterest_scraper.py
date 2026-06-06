"""Pinterest outfit image scraper using Playwright."""

import asyncio
import random
import re
import time
import urllib.parse
from pathlib import Path
from typing import List, Optional

from playwright.async_api import async_playwright, Browser, Page, TimeoutError as PlaywrightTimeout

from config.settings import get_settings
from database.sqlite_db import get_db
from drive.google_drive import get_drive_client
from filters.image_filter import get_image_filter
from utils.image_utils import download_image, get_image_hash, make_vertical
from utils.logger import get_logger

logger = get_logger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]


class ScrapingError(Exception):
    pass


def _best_image_url(url: str) -> str:
    """Upgrade Pinterest CDN URL to highest available resolution."""
    if "pinimg.com" in url:
        url = re.sub(r"/\d+x/", "/originals/", url)
        url = re.sub(r"/\d+x\d+_", "/originals/", url)
    return url


async def _extract_pin_images(page: Page, count: int = 30) -> List[str]:
    """Scroll the search results page and collect up to *count* image URLs."""
    urls: List[str] = []
    seen: set = set()
    last_height = 0
    stale_scrolls = 0

    while len(urls) < count and stale_scrolls < 5:
        images = await page.query_selector_all("img[src*='pinimg.com']")
        for img in images:
            try:
                src = await img.get_attribute("src") or ""
                srcset = await img.get_attribute("srcset") or ""
                # prefer largest from srcset
                if srcset:
                    parts = [p.strip() for p in srcset.split(",")]
                    # take last (largest) entry
                    src = parts[-1].split(" ")[0] if parts else src
                if not src or src in seen:
                    continue
                # filter out tiny profile pics (avatars < 50px usually have /30x30/)
                if re.search(r"/\d{1,2}x\d{1,2}/", src):
                    continue
                seen.add(src)
                urls.append(_best_image_url(src))
            except Exception:
                pass

        current_height = await page.evaluate("document.body.scrollHeight")
        if current_height == last_height:
            stale_scrolls += 1
        else:
            stale_scrolls = 0
        last_height = current_height

        await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
        await asyncio.sleep(random.uniform(1.5, 3.0))

    return list(dict.fromkeys(urls))[:count]


async def _scrape_keyword(
    browser: Browser,
    keyword: str,
    target_count: int,
    save_dir: Path,
    drive_folder_id: str,
) -> int:
    """Scrape images for one keyword. Returns number of new approved images saved."""
    db = get_db()
    image_filter = get_image_filter()
    saved = 0

    encoded = urllib.parse.quote_plus(keyword)
    url = f"https://www.pinterest.com/search/pins/?q={encoded}&rs=typed"

    context = await browser.new_context(
        user_agent=random.choice(_USER_AGENTS),
        viewport={"width": 1280, "height": 900},
        locale="en-US",
    )
    page = await context.new_page()

    try:
        logger.info("Scraping Pinterest for keyword: '{}'", keyword)
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(random.uniform(2.0, 4.0))

        # Detect login wall
        if await page.query_selector("input[name='id']") or "login" in page.url:
            logger.error("Pinterest login wall encountered — skipping keyword '{}'", keyword)
            return 0

        image_urls = await _extract_pin_images(page, count=max(50, target_count * 3))
        logger.info("Found {} candidate URLs for '{}'", len(image_urls), keyword)

        for img_url in image_urls:
            if saved >= target_count:
                break

            await asyncio.sleep(random.uniform(0.3, 0.8))

            try:
                filename = f"pinterest_{int(time.time() * 1000)}_{saved}.jpg"
                dest = save_dir / filename

                # Check URL duplicate first (fast)
                if db.is_duplicate_image(img_url, ""):
                    logger.debug("Duplicate URL, skipping: {}", img_url[:60])
                    continue

                download_image(img_url, dest)
                img_hash = get_image_hash(dest)

                if db.is_duplicate_image(img_url, img_hash):
                    logger.debug("Duplicate hash, skipping: {}", filename)
                    dest.unlink(missing_ok=True)
                    continue

                # AI quality filter
                filter_result = image_filter.check_image(dest)
                if not filter_result.approved:
                    logger.debug(
                        "Filter rejected {}: {}", filename, filter_result.reason
                    )
                    dest.unlink(missing_ok=True)
                    continue

                # Resize to vertical
                make_vertical(dest)

                # Upload to Drive
                drive_link = ""
                try:
                    drive_link = get_drive_client().upload_file(
                        dest, drive_folder_id, filename
                    )
                except Exception:
                    logger.warning("Drive upload failed for {} — storing locally only", filename)

                # Persist to DB
                db.insert_pinterest_image(
                    url=img_url,
                    local_path=str(dest),
                    drive_path=drive_link,
                    image_hash=img_hash,
                )
                saved += 1
                logger.info(
                    "Saved Pinterest image {}/{}: {}", saved, target_count, filename
                )

            except Exception:
                logger.exception("Error processing image URL: {}", img_url[:80])
                continue

    except PlaywrightTimeout:
        logger.error("Timeout loading Pinterest for keyword '{}'", keyword)
    except Exception:
        logger.exception("Unexpected error scraping keyword '{}'", keyword)
    finally:
        await context.close()

    return saved


async def _scrape_batch_async(
    keywords: List[str],
    target_count: int,
    save_dir: Path,
    drive_folder_id: str,
) -> int:
    total = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            per_keyword = max(2, target_count // max(len(keywords), 1))
            for keyword in keywords:
                saved = await _scrape_keyword(
                    browser, keyword, per_keyword, save_dir, drive_folder_id
                )
                total += saved
                if total >= target_count:
                    break
                await asyncio.sleep(random.uniform(3.0, 6.0))
        finally:
            await browser.close()
    return total


def scrape_batch(
    keywords: Optional[List[str]] = None,
    target_count: int = 10,
) -> int:
    """Main entry point. Scrapes Pinterest images and returns number saved.

    Keywords default to settings.pinterest_keywords when not provided.
    """
    settings = get_settings()
    kws = keywords or settings.pinterest_keywords
    if not kws:
        raise ScrapingError("No Pinterest keywords configured.")

    save_dir = settings.temp_folder / "pinterest"
    save_dir.mkdir(parents=True, exist_ok=True)

    drive_folder_id = settings.drive_folder_raw_pinterest_id or ""

    logger.info(
        "Starting Pinterest scrape: {} keywords, target {} images",
        len(kws),
        target_count,
    )
    total = asyncio.run(
        _scrape_batch_async(kws, target_count, save_dir, drive_folder_id)
    )
    logger.info("Pinterest scrape complete. Total saved: {}", total)
    return total
