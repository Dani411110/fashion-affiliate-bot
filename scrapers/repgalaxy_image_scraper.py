"""
RepGalaxy → Kakobuy image scraper.

Flow:
  1. Deschide repgalaxy.com/products/ cu Playwright
  2. Click "Load more" pana apar toate produsele
  3. Extrage toate link-urile kakobuy din carduri
  4. Pentru fiecare produs: deschide pagina kakobuy, asteapta sa se incarce
  5. Extrage TOATE pozele din galeria produsului
  6. Descarca pozele in data/repgalaxy_images/<slug-produs>/
  7. Adauga in tabelul pinterest_images din SQLite (optional, --no-db pentru skip)

Rulare standalone:
    python -m scrapers.repgalaxy_image_scraper
    python -m scrapers.repgalaxy_image_scraper --limit 10
    python -m scrapers.repgalaxy_image_scraper --limit 5 --no-db
"""

import argparse
import asyncio
import hashlib
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from playwright.async_api import (
    BrowserContext,
    Page,
    async_playwright,
)
from playwright.async_api import TimeoutError as PWTimeout

from utils.logger import get_logger

logger = get_logger(__name__)

# ── Constante ────────────────────────────────────────────────────────────────

REPGALAXY_URL = "https://repgalaxy.com/products/"
OUTPUT_DIR = Path("data/repgalaxy_images")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# Dimensiune minima pentru a considera o poza valida (pixeli)
MIN_IMG_SIZE = 300

# CDN-uri tipice pentru poze de produs de pe Weidian/Taobao/1688
PRODUCT_IMG_DOMAINS = (
    "alicdn.com",
    "weidian.com",
    "taobao.com",
    "tbcdn.cn",
    "sinaimg.cn",
    "img.kakobuy.com",
    "kakobuy.com",
    "img",          # fallback pentru orice subdomeniu de img
)

# URL-uri de ignorat (UI, logo-uri, icoane)
SKIP_URL_PATTERNS = (
    "logo",
    "icon",
    "avatar",
    "banner",
    "flag",
    "arrow",
    "loading",
    "placeholder",
    "spinner",
    "default",
    "blank",
    "noimage",
    "no-image",
    "/static/",
    "/assets/",
    "/fonts/",
    "svg",
    "gif",
    ".webp?w=",     # thumbnailuri mici webp
)

# Selectori pentru galeria de imagini pe kakobuy
KAKOBUY_GALLERY_SELECTORS = [
    # Swiper slideshow (cel mai comun pe site-urile de agent)
    ".swiper-slide img",
    ".swiper-wrapper img",
    # Galerie generica
    "[class*='gallery'] img",
    "[class*='Gallery'] img",
    "[class*='carousel'] img",
    "[class*='Carousel'] img",
    "[class*='slider'] img",
    "[class*='Slider'] img",
    # Imagini principale produs
    "[class*='product'] img",
    "[class*='Product'] img",
    "[class*='goods'] img",
    "[class*='Goods'] img",
    "[class*='detail'] img",
    "[class*='Detail'] img",
    # Thumbs
    "[class*='thumb'] img",
    "[class*='Thumb'] img",
    "[class*='preview'] img",
    # Fallback generic
    "img[src*='alicdn']",
    "img[src*='weidian']",
    "img[src*='sinaimg']",
    "img[src*='tbcdn']",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    """Transforma un nume de produs intr-un folder-name safe."""
    # Elimina caractere Unicode invizibile (RTL mark, zero-width space etc.)
    name = re.sub(r"[‎‏​‌‍﻿]", "", name)
    name = name.lower().strip()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_-]+", "-", name)
    name = name.strip("-")  # elimina liniutele de la inceput/sfarsit
    return name[:60] or "product"


def _is_product_image(url: str) -> bool:
    """Returneaza True daca URL-ul pare o poza de produs (nu UI)."""
    url_lower = url.lower()
    for skip in SKIP_URL_PATTERNS:
        if skip in url_lower:
            return False
    return True


def _normalize_img_url(src: str) -> str:
    """Curata URL-ul imaginii: adauga protocol, elimina parametri de resize."""
    if not src:
        return ""
    src = src.strip()
    if src.startswith("//"):
        src = "https:" + src
    # Elimina parametri de redimensionare tipici (.jpg_200x200.jpg → .jpg)
    # Alicdn: /photo/123_400x400.jpg → /photo/123.jpg
    src = re.sub(r"_\d+x\d+(\.\w+)$", r"\1", src)
    src = re.sub(r"\.\w+_\d+x\d+\.jpg$", ".jpg", src)
    # WordPress: image-300x300.jpg → image.jpg
    src = re.sub(r"-\d+x\d+(\.\w+)$", r"\1", src)
    # Elimina query string de resize
    src = re.sub(r"\?.*$", "", src)
    if not src.startswith("http"):
        return ""
    return src


def _img_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


# ── Pasul 1: Extrage link-urile Kakobuy de pe RepGalaxy ──────────────────────

async def _get_repgalaxy_products(page: Page) -> List[Dict[str, Any]]:
    """
    Incarca repgalaxy.com/products/, da click pe Load more de mai multe ori,
    si returneaza lista de produse cu kakobuy_url, name, thumbnail.
    """
    logger.info("Incarc RepGalaxy: {}", REPGALAXY_URL)
    await page.goto(REPGALAXY_URL, wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(2)

    # Click Load more pana dispare butonul
    # Selectorul real pe repgalaxy: DIV.jet-more-btn (nu button/a!)
    LOAD_MORE_SEL = (
        ".jet-more-btn, "
        ".jet-filter-items-moreless__toggle, "
        "a:has-text('Load more'), "
        "button:has-text('Load more'), "
        "[class*='load-more'], "
        "[class*='loadmore']"
    )
    load_more_clicks = 0
    prev_link_count = 0
    for _ in range(50):
        try:
            btn = await page.query_selector(LOAD_MORE_SEL)
            if not btn or not await btn.is_visible():
                break
            await btn.scroll_into_view_if_needed()
            await btn.click()
            load_more_clicks += 1
            await asyncio.sleep(2)
            # Daca nu au aparut produse noi dupa click, ne-am oprit
            new_count = len(await page.query_selector_all("a[href*='kakobuy'], a[href*='cnfans']"))
            if new_count == prev_link_count:
                break
            prev_link_count = new_count
        except Exception:
            break

    logger.info("Click-uri Load more: {} (produse finale vizibile: {})", load_more_clicks, prev_link_count or "?")

    # Extrage toate link-urile kakobuy de pe pagina
    all_links = await page.evaluate("""
        () => {
            const links = Array.from(document.querySelectorAll('a[href]'));
            return links
                .filter(a => a.href.includes('kakobuy.com') || a.href.includes('cnfans.com'))
                .map(a => {
                    // Cauta titlul in link-ul insusi SAU in cardul parinte
                    const titleSelectors = ['h2', 'h3', 'h4', 'h5',
                        '[class*="title"]', '[class*="name"]', '[class*="product-name"]'];
                    let title = '';

                    // 1. Titlul poate fi chiar in <a>
                    for (const sel of titleSelectors) {
                        const el = a.querySelector(sel);
                        if (el && el.innerText.trim()) {
                            title = el.innerText.trim();
                            break;
                        }
                    }

                    // 2. Sau in cardul parinte (mergi pana la 5 nivele sus)
                    if (!title) {
                        let parent = a.parentElement;
                        for (let i = 0; i < 5 && parent; i++) {
                            for (const sel of titleSelectors) {
                                const el = parent.querySelector(sel);
                                if (el && el.innerText.trim()) {
                                    title = el.innerText.trim();
                                    break;
                                }
                            }
                            if (title) break;
                            parent = parent.parentElement;
                        }
                    }

                    // 3. Fallback: alt text al imaginii
                    if (!title) {
                        const img = a.querySelector('img') ||
                            (a.closest('[class*="product"], article, li, .item') || a.parentElement)
                                ?.querySelector('img');
                        title = img ? (img.alt || img.title || '') : '';
                    }

                    const img = a.querySelector('img');
                    return {
                        href: a.href,
                        text: title.trim(),
                        imgSrc: img ? img.src : '',
                    };
                });
        }
    """)

    # Grupeaza link-urile pe produs: fiecare produs are un card cu imagine + link
    # Structura tipica: <a href="kakobuy..."><img ...></a> SAU card separat
    products = []
    seen_urls = set()

    for link_data in all_links:
        href = link_data.get("href", "").strip()
        img_src = link_data.get("img_src", "").strip()

        if not href or href in seen_urls:
            continue
        seen_urls.add(href)

        # Extrage numele produsului din text-ul cardului sau din URL
        text = link_data.get("text", "").strip()
        name = text[:100] if text else "product"

        products.append({
            "name": name,
            "kakobuy_url": href,
            "thumbnail": img_src,
        })

    # Daca nu am gasit prin link-uri, incearca carduri de produs
    if not products:
        logger.warning("Nu am gasit link-uri kakobuy prin metoda directa, incerc card extraction")
        products = await _extract_cards_fallback(page)

    logger.info("Produse gasite pe RepGalaxy: {}", len(products))
    return products


async def _extract_cards_fallback(page: Page) -> List[Dict[str, Any]]:
    """Fallback: extrage carduri de produs cu selectori CSS generici."""
    products = []
    seen_urls = set()

    # Evalueaza DOM direct in browser pentru performanta
    raw = await page.evaluate("""
        () => {
            const results = [];
            // Cauta orice element cu un link kakobuy
            document.querySelectorAll('*').forEach(el => {
                const links = el.querySelectorAll('a[href*="kakobuy"]');
                if (links.length === 0) return;
                links.forEach(a => {
                    const href = a.href;
                    if (!href) return;
                    // Cauta imagine in card
                    const card = a.closest('[class*="product"], article, li, .item') || a.parentElement;
                    const img = card ? card.querySelector('img') : null;
                    // Cauta titlu in card
                    const title = card ? (
                        card.querySelector('h2,h3,h4,[class*="title"],[class*="name"]')?.innerText || ''
                    ) : '';
                    results.push({
                        href,
                        title: title.trim(),
                        imgSrc: img ? img.src : '',
                    });
                });
            });
            return results;
        }
    """)

    for item in raw:
        href = (item.get("href") or "").strip()
        if not href or href in seen_urls:
            continue
        seen_urls.add(href)
        products.append({
            "name": (item.get("title") or "product").strip(),
            "kakobuy_url": href,
            "thumbnail": item.get("imgSrc", ""),
        })

    return products


# ── Pasul 2: Extrage imaginile de pe pagina Kakobuy ──────────────────────────

async def _extract_kakobuy_images(
    context: BrowserContext,
    kakobuy_url: str,
    product_name: str,
) -> Tuple[List[str], str]:
    """
    Deschide pagina kakobuy, asteapta sa se incarce galeria,
    si returneaza (lista URL-uri imagini, nume produs real de pe pagina).
    """
    page = await context.new_page()
    image_urls: List[str] = []
    real_name = product_name  # fallback la ce avem deja

    try:
        logger.info("  Kakobuy: {}", kakobuy_url[:80])
        await page.goto(kakobuy_url, wait_until="domcontentloaded", timeout=30_000)

        # Asteapta sa se incarce imaginile (max 8 secunde)
        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except PWTimeout:
            pass  # Continua chiar daca nu e networkidle
        await asyncio.sleep(2)

        # Extrage numele real al produsului de pe pagina kakobuy (daca nu l-am avut)
        if not real_name or real_name == "product":
            page_name = await page.evaluate("""
                () => {
                    const selectors = [
                        'h1', '[class*="title"]', '[class*="product-name"]',
                        '[class*="goods-name"]', '[class*="item-name"]',
                        'title'
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        const t = el ? (el.innerText || el.textContent || '').trim() : '';
                        if (t && t.length > 2 && t.length < 200) return t;
                    }
                    return '';
                }
            """)
            if page_name and page_name != "product":
                # Curata: elimina sufixe gen " - Kakobuy" sau " | ..."
                page_name = re.split(r'\s*[-|]\s*(Kakobuy|kakobuy|KakoBuy)', page_name)[0].strip()
                if page_name:
                    real_name = page_name
                    logger.info("  Nume extras din pagina kakobuy: {}", real_name[:50])

        # Scroll pentru a triggera lazy loading
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        await asyncio.sleep(1)
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.5)

        # Colecteaza imaginile prin mai multe strategii
        collected_urls: List[str] = []

        # Strategia 1: Selectori specifici pentru galeria kakobuy
        for selector in KAKOBUY_GALLERY_SELECTORS:
            try:
                imgs = await page.query_selector_all(selector)
                for img in imgs:
                    src = (
                        await img.get_attribute("src") or
                        await img.get_attribute("data-src") or
                        await img.get_attribute("data-lazy") or
                        await img.get_attribute("data-original") or
                        ""
                    )
                    if src:
                        collected_urls.append(src)
            except Exception:
                continue

        # Strategia 2: Extrage TOATE img src-urile din pagina via JS
        # (mai rapid decat query_selector_all pentru fiecare)
        all_srcs = await page.evaluate("""
            () => {
                const imgs = Array.from(document.querySelectorAll('img'));
                return imgs.map(img => ({
                    src: img.src || img.dataset.src || img.dataset.lazy || '',
                    w: img.naturalWidth || img.width || 0,
                    h: img.naturalHeight || img.height || 0,
                }));
            }
        """)

        for item in all_srcs:
            src = item.get("src", "")
            w = item.get("w", 0)
            h = item.get("h", 0)
            # Accepta daca dimensiunea e suficient de mare SAU necunoscuta (0)
            if src and (w == 0 or w >= MIN_IMG_SIZE) and (h == 0 or h >= MIN_IMG_SIZE):
                collected_urls.append(src)

        # Strategia 3: Intercepteaza request-urile de imagini (poze incarcate dinamic)
        # -- deja captate prin src la networkidle --

        # Deduplicare si filtrare
        seen = set()
        for url in collected_urls:
            norm = _normalize_img_url(url)
            if not norm or norm in seen:
                continue
            if not _is_product_image(norm):
                continue
            seen.add(norm)
            image_urls.append(norm)

        logger.info(
            "  Gasite {} poze pentru '{}' (din {} candidate)",
            len(image_urls), real_name[:40], len(collected_urls)
        )

    except Exception as exc:
        logger.error("  Eroare la {}: {}", kakobuy_url[:60], exc)
    finally:
        await page.close()

    return image_urls, real_name


# ── Pasul 3: Descarca imaginile ───────────────────────────────────────────────

def _download_images(
    urls: List[str],
    dest_dir: Path,
    product_name: str,
) -> List[Path]:
    """Descarca lista de URL-uri in dest_dir. Returneaza lista de fisiere descarcate."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": _USER_AGENT,
        "Referer": "https://www.kakobuy.com/",
    }
    downloaded: List[Path] = []

    for i, url in enumerate(urls, start=1):
        # Determina extensia
        parsed_path = urlparse(url).path
        ext = Path(parsed_path).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            ext = ".jpg"

        filename = f"img_{i:03d}{ext}"
        dest = dest_dir / filename

        if dest.exists():
            logger.debug("  Skip (exista): {}", filename)
            downloaded.append(dest)
            continue

        try:
            resp = requests.get(url, headers=headers, timeout=20, stream=True)
            resp.raise_for_status()

            content = resp.content
            # Verifica sa nu fie HTML (pagina de eroare)
            if content[:5] in (b"<!DOC", b"<html", b"<HTML") or b"<html" in content[:200]:
                logger.debug("  Skip HTML response: {}", url[:60])
                continue

            # Verifica dimensiunea minima (evita thumbnailuri 1x1)
            if len(content) < 5000:  # sub 5KB e probabil un thumbnail
                logger.debug("  Skip (prea mic {}B): {}", len(content), url[:60])
                continue

            dest.write_bytes(content)
            downloaded.append(dest)
            logger.debug("  Salvat: {} ({:.0f}KB)", filename, len(content) / 1024)

        except Exception as exc:
            logger.warning("  Nu am putut descarca {}: {}", url[:60], exc)

    return downloaded


# ── Pasul 4: Adauga in DB (optional) ─────────────────────────────────────────

def _add_to_db(image_paths: List[Path], product_name: str):
    """Adauga imaginile in tabelul pinterest_images ca stoc de postat."""
    try:
        from database.sqlite_db import get_db
        db = get_db()
        added = 0
        for path in image_paths:
            try:
                # Genereaza un URL placeholder (imaginea e locala)
                fake_url = f"file://repgalaxy/{product_name}/{path.name}"
                db.insert_pinterest_image(
                    url=fake_url,
                    local_path=str(path),
                    drive_path="",
                    image_hash="",
                )
                added += 1
            except Exception:
                pass  # IGNORE daca exista deja (UNIQUE constraint)
        if added:
            logger.info("  Adaugate {} poze in DB pentru '{}'", added, product_name[:40])
    except Exception as exc:
        logger.warning("  Nu am putut adauga in DB: {}", exc)


# ── Orchestrator principal ────────────────────────────────────────────────────

async def scrape_repgalaxy_images(
    limit: Optional[int] = None,
    add_to_db: bool = True,
    output_dir: Path = OUTPUT_DIR,
    headless: bool = True,
) -> Dict[str, Any]:
    """
    Scrapeaza imaginile de produs de pe RepGalaxy → Kakobuy.

    Args:
        limit:      Numarul maxim de produse de procesat (None = toate)
        add_to_db:  True = adauga in pinterest_images SQLite
        output_dir: Folderul unde se salveaza imaginile
        headless:   True = fara browser vizibil

    Returns:
        Dict cu statistici: products_processed, images_downloaded, errors
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = {
        "products_found": 0,
        "products_processed": 0,
        "images_downloaded": 0,
        "errors": 0,
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )

        # Blocheaza resurse inutile (fonturi, CSS) pentru viteza
        await context.route("**/*.{woff,woff2,ttf,otf}", lambda route: route.abort())

        main_page = await context.new_page()

        try:
            # Pasul 1: Ia produsele de pe RepGalaxy
            products = await _get_repgalaxy_products(main_page)
            stats["products_found"] = len(products)

            if limit:
                products = products[:limit]

            logger.info(
                "Procesez {} produse (din {} gasite)",
                len(products), stats["products_found"]
            )

            # Pasul 2+3: Pentru fiecare produs, ia pozele de pe kakobuy
            for idx, product in enumerate(products, start=1):
                name = product["name"] or f"product_{idx}"
                kakobuy_url = product["kakobuy_url"]

                logger.info("[{}/{}] {}", idx, len(products), name[:50])

                try:
                    # Extrage URL-urile imaginilor + numele real de pe pagina kakobuy
                    img_urls, real_name = await _extract_kakobuy_images(context, kakobuy_url, name)

                    # Foloseste numele real (de pe kakobuy) daca repgalaxy nu l-a avut
                    if real_name and real_name != "product" and real_name != name:
                        name = real_name

                    slug = _slugify(name)

                    if not img_urls:
                        logger.warning("  Nicio poza gasita pentru '{}'", name[:40])
                        stats["errors"] += 1
                        continue

                    # Descarca imaginile in folderul produsului
                    dest_dir = output_dir / slug
                    downloaded = _download_images(img_urls, dest_dir, name)

                    if downloaded:
                        logger.info(
                            "  Descarcate {}/{} poze in {}/",
                            len(downloaded), len(img_urls), slug
                        )
                        stats["images_downloaded"] += len(downloaded)
                        stats["products_processed"] += 1

                        # Pasul 4: Adauga in DB
                        if add_to_db:
                            _add_to_db(downloaded, name)
                    else:
                        logger.warning("  Nu s-a descarcat nimic pentru '{}'", name[:40])
                        stats["errors"] += 1

                except Exception as exc:
                    logger.error("  Eroare produs '{}': {}", name[:40], exc)
                    stats["errors"] += 1

                # Pauza mica intre produse
                await asyncio.sleep(1)

        finally:
            await browser.close()

    logger.info(
        "GATA: {} produse procesate, {} poze descarcate, {} erori",
        stats["products_processed"], stats["images_downloaded"], stats["errors"]
    )
    return stats


# ── Entry point ───────────────────────────────────────────────────────────────

def run(
    limit: Optional[int] = None,
    add_to_db: bool = True,
    output_dir: Path = OUTPUT_DIR,
    headless: bool = True,
) -> Dict[str, Any]:
    return asyncio.run(
        scrape_repgalaxy_images(
            limit=limit,
            add_to_db=add_to_db,
            output_dir=output_dir,
            headless=headless,
        )
    )


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Scrapeaza pozele de produs de pe RepGalaxy → Kakobuy"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Numarul maxim de produse (implicit: toate)"
    )
    parser.add_argument(
        "--no-db", action="store_true",
        help="Nu adauga in SQLite (doar descarca fisierele)"
    )
    parser.add_argument(
        "--output", type=str, default=str(OUTPUT_DIR),
        help=f"Folderul de output (implicit: {OUTPUT_DIR})"
    )
    parser.add_argument(
        "--visible", action="store_true",
        help="Deschide browser vizibil (debug)"
    )
    args = parser.parse_args()

    stats = run(
        limit=args.limit,
        add_to_db=not args.no_db,
        output_dir=Path(args.output),
        headless=not args.visible,
    )

    print("\n=== REZULTAT ===")
    print(f"Produse gasite pe RepGalaxy: {stats['products_found']}")
    print(f"Produse procesate:           {stats['products_processed']}")
    print(f"Poze descarcate total:       {stats['images_downloaded']}")
    print(f"Erori:                       {stats['errors']}")
    print(f"Salvate in: {args.output}")
