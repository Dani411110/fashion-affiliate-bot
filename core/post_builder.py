"""
Central post builder — orchestrates all modules into a complete PostPackage.

# PHASE 2 MIGRATION:
# PostBuilder becomes an async class driven by a Telegram command handler.
# build_post() is called via asyncio.run_in_executor in the bot's event loop.
# All file paths become URLs pointing to cloud storage (Supabase Storage).
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from captions.caption_generator import get_caption_generator
from categories.content_categories import get_category_selector
from config.settings import get_settings
from database.sqlite_db import get_db
from drive.google_drive import get_drive_client
from scrapers.mulebuy_scraper import get_cached_products, scrape_mulebuy
from scrapers.pinterest_scraper import scrape_batch
from utils.image_utils import download_image, make_vertical
from utils.logger import get_logger

logger = get_logger(__name__)


def _make_product_placeholder(product: Dict[str, Any], dest: Path):
    from PIL import Image, ImageDraw, ImageFont

    dest.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1080, 1920), color=(245, 245, 242))
    draw = ImageDraw.Draw(img)
    title = str(product.get("name", "Product"))[:80]
    price = f"${float(product.get('price', 0) or 0):.2f}"
    category = str(product.get("category", "")).title()
    try:
        font_big = ImageFont.truetype("arial.ttf", 64)
        font_mid = ImageFont.truetype("arial.ttf", 42)
    except Exception:
        font_big = ImageFont.load_default()
        font_mid = ImageFont.load_default()

    draw.text((90, 760), title, fill=(25, 25, 25), font=font_big)
    draw.text((90, 875), f"{category}  {price}", fill=(75, 75, 75), font=font_mid)
    draw.text((90, 1010), "Image unavailable - product kept in carousel", fill=(115, 115, 115), font=font_mid)
    img.save(dest, quality=92)


@dataclass
class PostPackage:
    post_id: int
    category: str
    # Image 1: Pinterest inspiration (local path)
    pinterest_image_path: str
    # Images 2-7: product local paths (in the same order as products)
    product_images: List[str]
    # All images in posting order: [pinterest] + product_images
    all_images: List[str]
    products: List[Dict[str, Any]]
    captions: Dict[str, Dict[str, Any]]        # raw: {platform: {title, caption, hashtags}}
    formatted_captions: Dict[str, str]          # ready-to-post string per platform
    drive_folder_id: str
    # Public URLs for each image (Drive), used by Instagram/TikTok carousel API
    public_image_urls: List[str] = field(default_factory=list)
    pinterest_image_id: Optional[int] = None
    status: str = "draft"


class PostBuilder:
    def __init__(self):
        self._settings = get_settings()
        self._db = get_db()

    def build_post(
        self,
        category_name: str,
        image_count: Optional[int] = None,
        allow_auto_scrape: bool = True,
    ) -> PostPackage:
        """Build a complete PostPackage (carousel, no video) for the given category."""
        logger.info("=== Building post: category='{}' ===", category_name)
        start = time.time()
        product_count: Optional[int] = None
        if image_count is not None:
            image_count = max(5, min(int(image_count), 8))
            product_count = image_count - 1

        # Step 1 — Load products from Mulebuy cache (scrape if needed)
        logger.info("[1/7] Loading products from Mulebuy cache")
        cached_products = get_cached_products(min_count=50, auto_scrape=allow_auto_scrape)
        if not cached_products:
            raise RuntimeError("No products available. Run .scrapeproducts 30 first.")

        # Step 2 — Select products via CategorySelector
        logger.info("[2/7] Selecting products via CategorySelector")
        exclude_ids = self._db.get_recently_used_product_rows(last_n_posts=10)
        selector = get_category_selector()
        selected_products = selector.select_by_name(
            category_name,
            cached_products,
            count=product_count,
            exclude_ids=exclude_ids,
        )
        if not selected_products:
            raise RuntimeError("CategorySelector returned no products")

        # Step 3 — Get unused Pinterest image (trigger scraper if stock low)
        logger.info("[3/7] Fetching Pinterest inspiration image")
        unused_count = self._db.count_unused_pinterest_images()
        if allow_auto_scrape and unused_count < self._settings.min_pinterest_stock:
            logger.info(
                "Pinterest stock low ({} < {}), triggering scrape",
                unused_count,
                self._settings.min_pinterest_stock,
            )
            try:
                scrape_batch(target_count=15)
            except Exception:
                logger.exception("Pinterest scrape failed — continuing with existing stock")

        unused_images = self._db.get_unused_pinterest_images(limit=5)
        if not unused_images:
            raise RuntimeError(
                "No unused Pinterest images available. Run: python main.py scrape"
            )

        import random
        pinterest_record = random.choice(unused_images)
        pinterest_image_path = Path(pinterest_record["local_path"])
        if not pinterest_image_path.exists():
            logger.warning(
                "Local Pinterest image missing: {} — re-downloading",
                pinterest_image_path,
            )
            download_image(pinterest_record["url"], pinterest_image_path)
            make_vertical(pinterest_image_path)

        # Step 4 — Download product images from Sheet image_url
        logger.info("[4/7] Downloading {} product images from Sheet", len(selected_products))
        temp_dir = self._settings.temp_folder / "products"
        temp_dir.mkdir(parents=True, exist_ok=True)
        product_image_paths: List[str] = []
        accepted_products: List[Dict[str, Any]] = []

        _MIN_DIM = 400  # pixels — skip thumbnails/low-res images

        for product in selected_products:
            img_url = product.get("image_url", "")
            dest = temp_dir / f"product_{product['sheet_row_index']}_{int(time.time() * 1000)}.jpg"
            if not img_url:
                logger.warning("Skipping product '{}' — no image_url", product.get("name"))
                continue
            try:
                download_image(img_url, dest)
                from PIL import Image as _PILImage
                with _PILImage.open(dest) as _im:
                    w, h = _im.size
                if w < _MIN_DIM or h < _MIN_DIM:
                    logger.warning(
                        "Skipping product '{}' — image too small ({}x{} < {}px)",
                        product.get("name"), w, h, _MIN_DIM,
                    )
                    dest.unlink(missing_ok=True)
                    continue
                product_image_paths.append(str(dest))
                accepted_products.append(product)
                logger.debug("Downloaded product image: {}x{} → {}", w, h, dest.name)
            except Exception:
                logger.exception("Failed to download product image: {}", img_url[:80])
                # Don't add placeholder — skip the product to keep carousel quality

        if not accepted_products:
            raise RuntimeError("All product images were too small or failed to download")

        selected_products = accepted_products
        logger.info(
            "{} products accepted after image quality filter (min {}px)",
            len(selected_products), _MIN_DIM,
        )

        # All images in carousel order: inspiration first, then products
        all_images = [str(pinterest_image_path)] + product_image_paths

        # Step 5 — Generate captions for all platforms
        logger.info("[5/7] Generating captions via GPT-4o")
        cap_gen = get_caption_generator()
        captions = cap_gen.generate_all_platforms(
            pinterest_image_path, selected_products, category_name
        )
        formatted: Dict[str, str] = {}
        for platform, cap_data in captions.items():
            formatted[platform] = cap_gen.format_for_platform(
                cap_data, selected_products, platform
            )

        # Step 6 — Upload images to Drive /Queue/ and get public URLs
        logger.info("[6/7] Uploading {} images to Google Drive /Queue/", len(all_images))
        drive_folder_id = self._settings.drive_folder_queue_id or ""
        public_image_urls: List[str] = []

        if drive_folder_id:
            for i, img_path in enumerate(all_images):
                p = Path(img_path)
                if not p.exists():
                    continue
                try:
                    drive = get_drive_client()
                    link = drive.upload_file(p, drive_folder_id, f"post_img_{i:02d}{p.suffix}")
                    # Convert sharing link to direct download URL for API use
                    file_id = drive.get_file_id_from_link(link)
                    if file_id:
                        direct_url = f"https://drive.google.com/uc?export=download&id={file_id}"
                        public_image_urls.append(direct_url)
                    else:
                        public_image_urls.append(link)
                except Exception:
                    logger.exception("Drive upload failed for image {}", i)
                    # Fall back to original source URLs where available
                    if i == 0:
                        public_image_urls.append(pinterest_record.get("url", ""))
                    elif (i - 1) < len(selected_products):
                        public_image_urls.append(
                            selected_products[i - 1].get("image_url", "")
                        )
        else:
            # No Drive folder — use source URLs directly
            logger.warning("DRIVE_FOLDER_QUEUE_ID not set — using source image URLs")
            public_image_urls.append(pinterest_record.get("url", ""))
            for p in selected_products:
                public_image_urls.append(p.get("image_url", ""))

        # Step 7 — Save post to SQLite as draft
        logger.info("[7/7] Saving post draft to SQLite")
        summary_caption = formatted.get("reddit", "")
        summary_hashtags = " ".join(
            f"#{h}" for h in captions.get("reddit", {}).get("hashtags", [])
        )
        post_id = self._db.create_post(
            category=category_name,
            pinterest_image_url=pinterest_record["url"],
            product_ids=[p["sheet_row_index"] for p in selected_products],
            caption=summary_caption,
            hashtags=summary_hashtags,
            video_path="",          # no video — carousel mode
            drive_folder_id=drive_folder_id,
            pinterest_local_path=str(pinterest_image_path),
            pinterest_image_id=pinterest_record["id"],
            image_paths=all_images,
            product_image_paths=product_image_paths,
            public_image_urls=public_image_urls,
            captions_json=captions,
            formatted_captions_json=formatted,
            carousel_image_count=len(all_images),
        )
        self._db.record_used_products(
            post_id, [p["sheet_row_index"] for p in selected_products]
        )

        elapsed = time.time() - start
        logger.info(
            "Post {} built in {:.1f}s: category='{}', {} products, {} images",
            post_id, elapsed, category_name, len(selected_products), len(all_images),
        )

        return PostPackage(
            post_id=post_id,
            category=category_name,
            pinterest_image_path=str(pinterest_image_path),
            product_images=product_image_paths,
            all_images=all_images,
            products=selected_products,
            captions=captions,
            formatted_captions=formatted,
            drive_folder_id=drive_folder_id,
            public_image_urls=public_image_urls,
            pinterest_image_id=pinterest_record["id"],
            status="draft",
        )


def package_from_db_record(record: Dict[str, Any]) -> PostPackage:
    """Rehydrate a PostPackage saved by PostBuilder for queue publishing."""
    product_ids = json.loads(record.get("product_ids") or "[]")
    cached_products = {
        p["sheet_row_index"]: p
        for p in get_db().get_all_cached_products()
    }
    products = [cached_products[pid] for pid in product_ids if pid in cached_products]

    def _json_list(key: str) -> List[str]:
        try:
            parsed = json.loads(record.get(key) or "[]")
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []

    def _json_dict(key: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(record.get(key) or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    product_images = _json_list("product_image_paths_json")
    all_images = _json_list("image_paths_json")
    pinterest_path = record.get("pinterest_local_path") or ""
    if not all_images and pinterest_path:
        all_images = [pinterest_path] + product_images

    captions = _json_dict("captions_json")
    formatted = _json_dict("formatted_captions_json")
    if not formatted and record.get("caption"):
        formatted = {"reddit": record.get("caption", "")}

    return PostPackage(
        post_id=record["id"],
        category=record["category"],
        pinterest_image_path=pinterest_path,
        product_images=product_images,
        all_images=all_images,
        products=products,
        captions=captions,
        formatted_captions=formatted,
        drive_folder_id=record.get("drive_folder_id", "") or "",
        public_image_urls=_json_list("public_image_urls_json"),
        pinterest_image_id=record.get("pinterest_image_id"),
        status=record.get("status", "draft"),
    )
