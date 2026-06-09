"""
Product image cache — pre-downloads all product images to the persistent volume.

Run via Telegram: .cacheimages
Run via CLI:      python -m scrapers.image_cache

Images are saved to PRODUCT_IMAGES_FOLDER (default: data/product_images/).
Each product gets a file named {sheet_row_index}.jpg.
Only downloads images >= 400x400px; skips products that already have a
local_image_path set in the DB.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Tuple

import requests
from PIL import Image as PILImage

from database.sqlite_db import get_db
from utils.logger import get_logger

logger = get_logger(__name__)

_MIN_DIM = 400
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_TIMEOUT = 20


def _download_one(product: dict, dest_dir: Path) -> Tuple[int, bool, str]:
    """Download a single product image. Returns (sheet_row_index, success, reason)."""
    idx = product["sheet_row_index"]
    url = product.get("image_url", "")
    if not url:
        return idx, False, "no image_url"

    dest = dest_dir / f"{idx}.jpg"

    try:
        resp = requests.get(url, timeout=_TIMEOUT, headers=_HEADERS)
        resp.raise_for_status()

        content = resp.content
        # Reject HTML responses (Drive virus-scan page, login redirects)
        if content[:5] in (b"<!DOC", b"<html", b"<HTML") or b"<html" in content[:200]:
            return idx, False, "got HTML instead of image"

        # Write temp then check dimensions
        dest.write_bytes(content)
        with PILImage.open(dest) as im:
            w, h = im.size
            # Convert to RGB JPEG if needed
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
                im.save(dest, "JPEG", quality=92, optimize=True)

        if w < _MIN_DIM or h < _MIN_DIM:
            dest.unlink(missing_ok=True)
            return idx, False, f"too small ({w}x{h})"

        return idx, True, str(dest)

    except Exception as exc:
        dest.unlink(missing_ok=True)
        return idx, False, str(exc)[:80]


def cache_product_images(max_workers: int = 5, force: bool = False) -> dict:
    """Download all missing product images to the persistent volume.

    Args:
        max_workers: parallel download threads (keep low on Railway free tier)
        force: re-download even if local_image_path already set

    Returns:
        dict with keys: downloaded, skipped, failed, total
    """
    from config.settings import get_settings
    db = get_db()
    dest_dir = get_settings().product_images_folder
    dest_dir.mkdir(parents=True, exist_ok=True)

    if force:
        products = db.get_all_cached_products()
    else:
        products = db.get_products_without_local_image()

    total = len(products)
    if total == 0:
        logger.info("All product images already cached.")
        return {"downloaded": 0, "skipped": 0, "failed": 0, "total": 0}

    logger.info("Caching {} product images → {}", total, dest_dir)
    downloaded = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_download_one, p, dest_dir): p for p in products}
        for future in as_completed(futures):
            idx, success, info = future.result()
            if success:
                db.update_product_local_image(idx, info)
                downloaded += 1
                if downloaded % 10 == 0:
                    logger.info("  cached {}/{}", downloaded, total)
            else:
                failed += 1
                logger.debug("  skip #{}: {}", idx, info)

    skipped = total - downloaded - failed
    logger.info(
        "Image cache complete: {} downloaded, {} failed out of {}",
        downloaded, failed, total,
    )
    return {"downloaded": downloaded, "skipped": skipped, "failed": failed, "total": total}


if __name__ == "__main__":
    result = cache_product_images()
    print(f"Done: {result['downloaded']} downloaded, {result['failed']} failed / {result['total']} total")
