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
from utils.image_utils import download_image
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
        # Request 3x the needed count as buffer — image download failures and
        # quality rejections won't leave us short of the requested image_count.
        logger.info("[2/7] Selecting products via CategorySelector")
        exclude_ids = self._db.get_recently_used_product_rows(last_n_posts=10)
        selector = get_category_selector()
        # Request at least 3× the needed products so we have enough after filtering
        _QUALITY_BUFFER = max(15, (product_count or 0) * 2)
        candidate_count = ((product_count or 0) + _QUALITY_BUFFER) if product_count else None
        selected_products = selector.select_by_name(
            category_name,
            cached_products,
            count=candidate_count,
            exclude_ids=exclude_ids,
        )
        if not selected_products:
            raise RuntimeError("CategorySelector returned no products")

        # Step 3 — Prima poza: foto Pinterest cu outfit (persoana cu haine)
        # Pozele din repgalaxy sunt imagini de produs si se folosesc la Step 4.
        logger.info("[3/7] Fetching Pinterest outfit photo (prima poza din carusel)")
        import random

        unused_count = self._db.count_unused_pinterest_images()
        if allow_auto_scrape and unused_count < self._settings.min_pinterest_stock:
            logger.info(
                "Pinterest stock scazut ({} < {}), triggerez scrape",
                unused_count, self._settings.min_pinterest_stock,
            )
            try:
                scrape_batch(target_count=15)
            except Exception:
                logger.exception("Pinterest scrape failed — continuam cu stocul existent")

        # Ia doar pozele reale Pinterest (nu repgalaxy) pentru prima poza
        unused_images = self._db.get_pinterest_outfit_images(limit=10)
        if not unused_images:
            # Fallback: orice poza disponibila
            unused_images = self._db.get_unused_pinterest_images(limit=10)
        if not unused_images:
            raise RuntimeError(
                "Nicio poza Pinterest disponibila. Ruleaza: python main.py scrape 20"
            )

        pinterest_record = random.choice(unused_images)
        pinterest_image_path = Path(pinterest_record["local_path"])
        if not pinterest_image_path.exists():
            logger.warning("Poza Pinterest lipsa local: {} — redownload", pinterest_image_path)
            download_image(pinterest_record["url"], pinterest_image_path)
        logger.info("Prima poza (Pinterest outfit): {}", pinterest_image_path.name)

        # Step 4 — Pozele de produs: din repgalaxy_images/ (daca exista), altfel mulebuy
        logger.info("[4/7] Selectez pozele de produs")
        product_image_paths: List[str] = []
        accepted_products: List[Dict[str, Any]] = []

        # ── Sursa 1: data/repgalaxy_images/ ─────────────────────────────────
        repgalaxy_dir = Path("data/repgalaxy_images")
        repgalaxy_all: List[Path] = []
        if repgalaxy_dir.exists():
            for subdir in repgalaxy_dir.iterdir():
                if subdir.is_dir():
                    for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
                        repgalaxy_all.extend(subdir.glob(ext))

        if repgalaxy_all:
            needed = product_count if product_count else self._settings.max_products_per_post
            # Alege random din FOLDER-URI DIFERITE (varietate)
            subdirs = [p for p in repgalaxy_dir.iterdir() if p.is_dir()]
            random.shuffle(subdirs)
            chosen_paths: List[Path] = []
            for subdir in subdirs:
                if len(chosen_paths) >= needed:
                    break
                imgs = list(subdir.glob("*.jpg")) + list(subdir.glob("*.jpeg")) + \
                       list(subdir.glob("*.png")) + list(subdir.glob("*.webp"))
                if imgs:
                    chosen_paths.append(random.choice(imgs))

            # Daca avem prea putine foldere, mai adaugam random din toate
            if len(chosen_paths) < needed:
                remaining = [p for p in repgalaxy_all if p not in chosen_paths]
                random.shuffle(remaining)
                chosen_paths.extend(remaining[:needed - len(chosen_paths)])

            for path in chosen_paths[:needed]:
                product_image_paths.append(str(path.resolve()))
                # Creeaza un produs sintetic (necesar pentru captionuri)
                accepted_products.append({
                    "sheet_row_index": hash(str(path)) % 999999,
                    "name": path.parent.name.replace("-", " ").title(),
                    "image_url": f"file://{path}",
                    "mulebuy_link": "",
                    "category": category_name,
                    "price": 0.0,
                    "tags": category_name,
                })

            logger.info(
                "{} poze de produs din repgalaxy_images/ (cerut {})",
                len(product_image_paths), needed,
            )
        else:
            # ── Sursa 2: fallback — descarca de la mulebuy ───────────────────
            logger.info("repgalaxy_images/ gol, descarc de la mulebuy ({} candidati)", len(selected_products))
            temp_dir = self._settings.temp_folder / "products"
            temp_dir.mkdir(parents=True, exist_ok=True)
            _MIN_DIM = 150

            for product in selected_products:
                if product_count is not None and len(accepted_products) >= product_count:
                    break
                cached_path = product.get("local_image_path", "")
                if cached_path and Path(cached_path).exists():
                    product_image_paths.append(cached_path)
                    accepted_products.append(product)
                    continue
                img_url = product.get("image_url", "")
                if not img_url:
                    continue
                dest = temp_dir / f"product_{product['sheet_row_index']}_{int(time.time() * 1000)}.jpg"
                try:
                    download_image(img_url, dest)
                    from PIL import Image as _PILImage
                    with _PILImage.open(dest) as _im:
                        w, h = _im.size
                    if w < _MIN_DIM or h < _MIN_DIM:
                        dest.unlink(missing_ok=True)
                        continue
                    product_image_paths.append(str(dest))
                    accepted_products.append(product)
                except Exception:
                    logger.exception("Failed to download product image: {}", img_url[:80])

            if product_count is not None and len(accepted_products) < product_count:
                logger.warning("Cerut {} produse, obtinut {}", product_count, len(accepted_products))

        if not accepted_products:
            raise RuntimeError("Nicio poza de produs disponibila. Ruleaza scrape-repgalaxy.")

        selected_products = accepted_products
        logger.info("{} poze de produs selectate", len(selected_products))

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
