"""
Telegram bot — replaces CLI approval interface in cloud/server mode.

Flow:
  APScheduler triggers at 9:00, 14:00, 19:00
  → Bot trimite mesaj "Selecteaza categoria"
  → User alege categoria cu butoane inline
  → Bot construieste postul (~20s)
  → Bot trimite preview: poza + produse + captioane
  → User apasa: ✅ Aprobare / ❌ Respinge / 🔄 Regenerare
  → Bot publica pe platforme si confirma

# PHASE 2 MIGRATION: Acesta este modulul Phase 2.
# Inlocuieste core/approval_interface.py si core/scheduler.py CLI.
"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from textwrap import shorten
from typing import Any, Dict, Optional

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode

from categories.content_categories import CATEGORY_NAMES
from config.settings import get_settings
from database.sqlite_db import get_db
from utils.logger import get_logger

logger = get_logger(__name__)

# Thread pool pentru operatiuni blocking (build_post, publish)
_executor = ThreadPoolExecutor(max_workers=2)

# State per sesiune: chat_id → {post_index, packages, waiting_for_category}
_sessions: Dict[int, Dict[str, Any]] = {}

# Posturi pending aprobare: post_id → PostPackage
_pending: Dict[int, Any] = {}

POSTS_PER_SESSION = 3


# ── Helpers ───────────────────────────────────────────────────────────────────

def _category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Generează post", callback_data="cat:0")],
    ])


def _image_count_keyboard(category_num: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(str(count), callback_data=f"count:{category_num}:{count}")
            for count in range(5, 9)
        ]
    ])


def _approval_keyboard(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Aprobare", callback_data=f"approve:{post_id}"),
            InlineKeyboardButton("❌ Respinge", callback_data=f"reject:{post_id}"),
        ],
        [
            InlineKeyboardButton("🔄 Regenerare captioane", callback_data=f"regen:{post_id}"),
        ],
    ])


def _prepare_keyboard(post_id: int) -> InlineKeyboardMarkup:
    """Butoane pentru modul .prepare (adauga in coada, nu posta imediat)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Adaugă în coadă", callback_data=f"p_add:{post_id}")],
        [
            InlineKeyboardButton("🔄 Poze noi", callback_data=f"p_regen:{post_id}"),
            InlineKeyboardButton("✏️ Captions noi", callback_data=f"p_caps:{post_id}"),
        ],
        [InlineKeyboardButton("❌ Skip", callback_data=f"p_skip:{post_id}")],
    ])


def _platform_keyboard(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ YouTube",  callback_data=f"pub:{post_id}:youtube"),
            InlineKeyboardButton("🎵 TikTok",   callback_data=f"pub:{post_id}:tiktok"),
        ],
        [
            InlineKeyboardButton("📸 Instagram", callback_data=f"pub:{post_id}:instagram"),
        ],
        [
            InlineKeyboardButton("🌐 Toate platformele", callback_data=f"pub:{post_id}:all"),
        ],
    ])


async def _run_in_thread(fn, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, fn, *args)


def _positive_int_arg(context: ContextTypes.DEFAULT_TYPE, default: int, maximum: int) -> int:
    if not context.args:
        return default
    try:
        value = int(context.args[0])
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))


async def _send_start_menu(update: Update):
    await update.message.reply_text(
        "*Fashion Affiliate Bot*\n\n"
        "Alege ce tip de post vrei sa construiesc acum. Iti trimit preview-ul, "
        "apoi alegi daca il postam, il respingem sau regeneram captionul.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_category_keyboard(),
    )


def _help_text() -> str:
    return (
        "*Fashion Bot commands*\n\n"
        ".prepare - construieste 3 posturi si le adauga in coada (recomandat)\n"
        ".run - sesiune de 3 posturi cu publicare imediata\n"
        ".start - construieste un post individual\n"
        ".status - statistici DB\n"
        ".queue - ultimele postari si coada\n"
        ".platforms - platforme active/inactive\n"
        ".scrape 50 - scrape Pinterest\n"
        ".scrapeproducts 30 - scrape Mulebuy\n"
        ".syncsheet - sync Google Sheet in SQLite\n"
        ".cacheimages - descarca toate imaginile produselor local\n\n"
        "Flow recomandat: .prepare -> aproba fiecare post -> botul posteaza automat la 08:00 / 13:00 / 20:00"
    )


def _platform_status_lines() -> list[str]:
    settings = get_settings()
    return [
        f"Instagram: {'ON' if settings.enable_instagram else 'OFF'}",
        f"TikTok: {'ON' if settings.enable_tiktok else 'OFF'}",
        f"YouTube: {'ON' if settings.enable_youtube else 'OFF'}",
    ]


def _readiness_text() -> str:
    settings = get_settings()
    stats = get_db().get_stats()
    drive_ready = all(
        [
            settings.drive_folder_queue_id,
            settings.drive_folder_posted_id,
            settings.drive_folder_rejected_id,
        ]
    )
    checks = [
        ("OpenAI", bool(settings.openai_api_key), "set"),
        ("Telegram", bool(settings.telegram_bot_token and settings.telegram_chat_id), "connected"),
        ("SQLite", True, f"{stats.get('products_cached', 0)} products"),
        ("Pinterest stock", int(stats.get("pinterest_unused", 0) or 0) > 0, f"{stats.get('pinterest_unused', 0)} unused"),
        ("Drive folders", drive_ready, "Queue/Posted/Rejected IDs"),
        ("TikTok OAuth app", bool(settings.tiktok_client_key and settings.tiktok_client_secret), "waiting review/token"),
        ("YouTube OAuth", bool(settings.youtube_client_secrets_json and Path("data/youtube_token.json").exists()), "token needed"),
        ("Instagram", bool(settings.instagram_access_token and settings.instagram_user_id), "pending Meta"),
    ]
    lines = ["*Readiness*", ""]
    for name, ok, detail in checks:
        mark = "OK" if ok else "MISSING"
        lines.append(f"{name}: {mark} - {detail}")
    return "\n".join(lines)


def _format_queue_rows() -> str:
    db = get_db()
    stats = db.get_stats()
    rows = db.get_recent_posts(limit=8)
    lines = ["*Queue / Posts*", ""]
    for status, count in sorted(stats.get("posts_by_status", {}).items()):
        lines.append(f"{status}: {count}")
    if not rows:
        lines.append("\nNu exista postari in DB.")
        return "\n".join(lines)

    lines.append("\n*Ultimele postari:*")
    for row in rows:
        caption = _escape_md(shorten((row.get("caption") or "").replace("\n", " "), width=70, placeholder="..."))
        lines.append(
            f"#{row['id']} | {_escape_md(str(row['status']))} | {_escape_md(str(row['category']))} | {row.get('carousel_image_count', 0)} poze"
        )
        if caption:
            lines.append(f"  {caption}")
    return "\n".join(lines)


def _stock_blocking_message(image_count: int) -> Optional[str]:
    stats = get_db().get_stats()
    needed_products = max(1, image_count - 1)
    products = int(stats.get("products_cached", 0) or 0)
    pinterest_unused = int(stats.get("pinterest_unused", 0) or 0)
    problems = []
    if products < needed_products:
        problems.append(f"produse Mulebuy: {products}/{needed_products}")
    if pinterest_unused < 1:
        problems.append("poze Pinterest nefolosite: 0/1")
    if not problems:
        return None
    return (
        "*Nu pot construi inca postarea.*\n\n"
        "Stock insuficient in DB-ul din cloud:\n"
        + "\n".join(f"- {item}" for item in problems)
        + "\n\nRuleaza intai:\n"
        "`.scrapeproducts 30`\n"
        "`.scrape 20`\n\n"
        "Dupa ce dashboardul arata produse si poze, ruleaza din nou `.start`."
    )


def _escape_md(text: str) -> str:
    """Escape Markdown v1 special chars in dynamic content."""
    for ch in r"\_*`[":
        text = text.replace(ch, f"\\{ch}")
    return text


def _format_preview(pkg) -> str:
    lines = [
        f"*Post #{pkg.post_id}*",
        f"Categoria: {_escape_md(str(pkg.category))}",
        f"Carusel: {len(pkg.all_images)} imagini",
        "",
        "*Produse:*",
    ]
    for idx, p in enumerate(pkg.products, start=1):
        raw_name = shorten(str(p.get("name", "Product")), width=42, placeholder="...")
        price = float(p.get("price", 0) or 0)
        link = p.get("mulebuy_link", "")
        price_str = f" - ${price:.2f}" if price > 0 else ""
        if link:
            # Strip chars that break link display text in Markdown v1
            safe_name = raw_name.replace("]", "").replace("[", "").replace("_", " ").replace("*", "")
            lines.append(f"{idx}. [{safe_name}]({link}){price_str}")
        else:
            lines.append(f"{idx}. {_escape_md(raw_name)}{price_str}")

    tiktok = _escape_md(shorten(pkg.formatted_captions.get("tiktok", ""), width=500, placeholder="..."))
    instagram = _escape_md(shorten(pkg.formatted_captions.get("instagram", ""), width=500, placeholder="..."))

    lines += ["", "*TikTok:*", tiktok]
    lines += ["", "*Instagram:*", instagram]
    return "\n".join(lines)

_TARGET_W = 1080
_TARGET_H = 1350  # 4:5 portrait — standard social media


def _to_jpeg_bytes(path: Path) -> bytes:
    """Resize to phone-friendly 4:5 portrait using crop-to-fill.

    Scales the image so it fully covers 1080x1350, then center-crops.
    No background added — original image fills the entire frame.
    """
    from PIL import Image as _PIL
    import io

    with _PIL.open(path) as im:
        im = im.convert("RGB")

        # Scale so image covers entire 1080x1350 (whichever side hits first, scale by the other)
        scale = max(_TARGET_W / im.width, _TARGET_H / im.height)
        new_w = int(im.width * scale)
        new_h = int(im.height * scale)
        im = im.resize((new_w, new_h), _PIL.LANCZOS)

        # Center crop to exactly 1080x1350
        left = (new_w - _TARGET_W) // 2
        top = (new_h - _TARGET_H) // 2
        im = im.crop((left, top, left + _TARGET_W, top + _TARGET_H))

        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=92, optimize=True)
        return buf.getvalue()


async def _send_post_preview(bot: Bot, chat_id: int, pkg, keyboard=None):
    """Trimite preview complet al postului cu butoane de aprobare."""
    _pending[pkg.post_id] = pkg

    image_paths = []
    for p in pkg.all_images:
        if not p:
            continue
        resolved = Path(p)
        if not resolved.is_absolute():
            resolved = (Path(__file__).parent.parent / p).resolve()
        if resolved.exists():
            image_paths.append(resolved)
        else:
            logger.warning("Poza lipsa: {}", p)
    logger.info("Preview: {} imagini valide din {} total", len(image_paths), len(pkg.all_images))

    sent = 0
    if len(image_paths) >= 2:
        import io
        media = []
        raw_bufs = []
        for idx, path in enumerate(image_paths[:10]):
            try:
                data = _to_jpeg_bytes(path)
                buf = io.BytesIO(data)
                buf.name = f"img_{idx:02d}.jpg"
                raw_bufs.append(buf)
                media.append(
                    InputMediaPhoto(
                        media=buf,
                        caption=f"Preview carusel ({len(image_paths)} poze)" if idx == 0 else None,
                    )
                )
            except Exception as exc:
                logger.warning("Poza {} nu poate fi convertita: {}", path.name, exc)

        if len(media) >= 2:
            try:
                await bot.send_media_group(chat_id=chat_id, media=media)
                sent = len(media)
            except Exception as exc:
                logger.warning("Media group esuat ({}), trimit individual...", exc)
                for i, (buf, path) in enumerate(zip(raw_bufs, image_paths)):
                    try:
                        buf.seek(0)
                        await bot.send_photo(chat_id=chat_id, photo=buf)
                        sent += 1
                    except Exception as exc2:
                        logger.warning("Poza {} esuata individual: {}", path.name, exc2)
        elif media:
            try:
                raw_bufs[0].seek(0)
                await bot.send_photo(chat_id=chat_id, photo=raw_bufs[0], caption="Preview carusel")
                sent = 1
            except Exception as exc:
                logger.warning("Trimitere poza unica esuata: {}", exc)
    elif image_paths:
        try:
            import io
            data = _to_jpeg_bytes(image_paths[0])
            buf = io.BytesIO(data)
            await bot.send_photo(chat_id=chat_id, photo=buf, caption="Preview carusel")
            sent = 1
        except Exception as exc:
            logger.warning("Nu am putut trimite nicio poza: {}", exc)

    if sent == 0:
        logger.warning("Nicio poza trimisa in preview pentru post {}", pkg.post_id)

    text = _format_preview(pkg)
    await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard if keyboard is not None else _approval_keyboard(pkg.post_id),
    )

async def _publish_package(pkg, platforms: set = None):
    """Publica postul pe platformele selectate (sau toate daca platforms=None)."""
    from config.settings import get_settings
    settings = get_settings()

    def _want(name: str) -> bool:
        if platforms is None or "all" in platforms:
            return True
        return name in platforms

    def _do_publish():
        publishers = []
        if settings.enable_instagram and _want("instagram"):
            from publishers.instagram_publisher import InstagramPublisher
            publishers.append(InstagramPublisher(
                settings.instagram_access_token, settings.instagram_user_id
            ))
        if settings.enable_tiktok and _want("tiktok"):
            from publishers.tiktok_publisher import TikTokPublisher
            publishers.append(TikTokPublisher(
                settings.tiktok_cookies_path, settings.tiktok_access_token
            ))
        if settings.enable_youtube and _want("youtube"):
            from publishers.youtube_publisher import YouTubePublisher
            publishers.append(YouTubePublisher(
                settings.youtube_client_secrets_json,
                token_path=Path(settings.youtube_token_path),
                token_json=settings.youtube_token_json or None,
                seconds_per_image=settings.video_seconds_per_image,
            ))

        r = []
        for pub in publishers:
            result = pub.publish(pkg)
            r.append((pub.platform_name, result))
        return r

    results = await _run_in_thread(_do_publish)
    if any(result.success for _, result in results):
        get_db().mark_post_status(pkg.post_id, "posted")
    return results


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return
    await _send_start_menu(update)


async def cmd_dot_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return
    await _send_start_menu(update)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return
    await update.message.reply_text(_help_text(), parse_mode=ParseMode.MARKDOWN)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return
    stats = get_db().get_stats()
    text = (
        f"📊 *Status Bot*\n\n"
        f"🖼 Poze Pinterest: {stats['pinterest_unused']}/{stats['pinterest_total']}\n"
        f"🛍 Produse cache: {stats['products_cached']}\n"
        f"📝 Posturi total: {stats['posts_total']}\n"
    )
    for status, count in stats.get("posts_by_status", {}).items():
        text += f"   • {status}: {count}\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_platforms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return
    await update.message.reply_text(
        "*Platforme*\n\n" + "\n".join(_platform_status_lines()),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return
    await update.message.reply_text(_format_queue_rows(), parse_mode=ParseMode.MARKDOWN)


async def cmd_readiness(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return
    await update.message.reply_text(_readiness_text(), parse_mode=ParseMode.MARKDOWN)


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Porneste o sesiune manuala de 3 posturi (publica imediat)."""
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return

    chat_id = update.effective_chat.id
    _sessions[chat_id] = {
        "post_index": 0,
        "packages": [],
        "mode": "run",
    }
    await _ask_category(context.bot, chat_id)


async def cmd_prepare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Construieste 3 posturi si le adauga in coada pentru postare automata."""
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return

    chat_id = update.effective_chat.id
    existing = get_db().get_posts_by_status("scheduled")
    if existing:
        await update.message.reply_text(
            f"⚠️ Ai deja {len(existing)} post(uri) în coadă.\n"
            "Continuă adăugând mai multe sau așteaptă să fie publicate.",
            parse_mode=ParseMode.MARKDOWN,
        )

    _sessions[chat_id] = {
        "post_index": 0,
        "packages": [],
        "mode": "prepare",
    }
    await update.message.reply_text(
        "📅 *Mod pregătire coadă*\n\n"
        "Construiesc 3 posturi pe care le vei aproba în avans.\n"
        "La fiecare oră programată (08:00, 13:00, 20:00) botul va publica automat primul post din coadă.",
        parse_mode=ParseMode.MARKDOWN,
    )
    await _ask_category(context.bot, chat_id)


async def cmd_postqueue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return

    approved = get_db().get_posts_by_status("approved")
    if not approved:
        await update.message.reply_text("Nu exista posturi aprobate in coada.")
        return

    await update.message.reply_text(f"Public {len(approved)} posturi aprobate din coada...")
    from core.post_builder import package_from_db_record

    for record in approved:
        try:
            pkg = package_from_db_record(record)
            results = await _publish_package(pkg)
            if not results:
                await update.message.reply_text(
                    f"Post #{pkg.post_id}: aprobat, dar nicio platforma nu este activa."
                )
                continue
            lines = [f"*Post #{pkg.post_id}*"]
            for platform, result in results:
                if result.success:
                    lines.append(f"{platform.upper()}: {_escape_md(str(result.url or result.platform_post_id or ''))}")
                else:
                    lines.append(f"{platform.upper()} failed: {_escape_md(str(result.error or '')[:100])}")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:
            logger.exception("Queued publish failed for post {}", record.get("id"))
            await update.message.reply_text(
                f"Post #{record.get('id')}: eroare postqueue: {exc}"
            )


async def cmd_scrape(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return
    count = _positive_int_arg(context, default=15, maximum=100)
    await update.message.reply_text(f"Scrape Pinterest pentru {count} poze... (1-5 minute)")

    def _do_scrape():
        from scrapers.pinterest_scraper import scrape_batch
        return scrape_batch(target_count=count)

    total = await _run_in_thread(_do_scrape)
    if total is not None and total < 0:
        blocked = abs(total)
        await update.message.reply_text(
            f"Pinterest BLOCAT: {blocked} keyword(s) au dat login wall.\n"
            "IP-ul Railway este probabil blocat de Pinterest.\n"
            "Solutii:\n"
            "1. Adauga proxy HTTP in settings (PINTEREST_PROXY)\n"
            "2. Incarca pozele manual in /data/pinterest/"
        )
    elif not total:
        await update.message.reply_text(
            "0 poze salvate de pe Pinterest.\n"
            "Posibile cauze: IP blocat, fara imagini noi, sau eroare de retea.\n"
            "Vezi logurile Railway pentru detalii."
        )
    else:
        await update.message.reply_text(f"Salvate {total} poze noi de pe Pinterest.")

async def cmd_scrapeproducts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return
    per_category = _positive_int_arg(context, default=20, maximum=100)
    await update.message.reply_text(
        f"Scrape Mulebuy produse ({per_category}/categorie)... (5-15 minute)"
    )

    def _do_scrape():
        from scrapers.mulebuy_scraper import scrape_mulebuy
        return scrape_mulebuy(per_category=per_category)

    products = await _run_in_thread(_do_scrape)
    await update.message.reply_text(f"Salvate {len(products)} produse noi din Mulebuy.")


async def cmd_resetpinterest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return
    count = get_db().reset_all_pinterest_images()
    await update.message.reply_text(
        f"✅ {count} poze Pinterest marcate ca nefolosite. Poti rula .start din nou."
    )


async def cmd_resetmulebuy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return
    count = get_db().reset_all_mulebuy_products()
    await update.message.reply_text(
        f"✅ Istoricul de produse Mulebuy resetat ({count} inregistrari sterse). Toate produsele sunt disponibile din nou."
    )


async def cmd_resetall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return
    result = get_db().reset_all_used()
    await update.message.reply_text(
        f"✅ Reset complet:\n"
        f"- {result['pinterest']} poze Pinterest marcate ca nefolosite\n"
        f"- {result['mulebuy']} inregistrari produse Mulebuy sterse\n\n"
        f"Poti rula .start din nou."
    )


async def cmd_cacheimages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return
    pending = get_db().get_products_without_local_image()
    if not pending:
        await update.message.reply_text("✅ Toate imaginile produselor sunt deja descărcate local.")
        return
    await update.message.reply_text(
        f"⬇️ Descarc imaginile pentru {len(pending)} produse... (poate dura câteva minute)"
    )

    def _do_cache():
        from scrapers.image_cache import cache_product_images
        return cache_product_images(max_workers=5)

    try:
        result = await _run_in_thread(_do_cache)
        await update.message.reply_text(
            f"✅ Cache imagini complet!\n"
            f"• Descărcate: {result['downloaded']}\n"
            f"• Eșuate: {result['failed']}\n"
            f"• Total: {result['total']}"
        )
    except Exception as exc:
        logger.exception("Cache images failed")
        await update.message.reply_text(f"❌ Eroare cache imagini: {exc}")


async def cmd_syncsheet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return
    await update.message.reply_text("Sync Google Sheet in SQLite...")

    def _do_sync():
        from sheets.google_sheets import get_sheets_client
        products = get_sheets_client().get_all_products(force_refresh=True)
        get_db().sync_products(products)
        return len(products)

    try:
        count = await _run_in_thread(_do_sync)
        await update.message.reply_text(f"Sync complet: {count} produse.")
    except Exception as exc:
        logger.exception("Google Sheet sync failed")
        await update.message.reply_text(f"Eroare sync Google Sheet: {exc}")

# Session flow ──────────────────────────────────────────────────────────────

async def _ask_category(bot: Bot, chat_id: int):
    session = _sessions.get(chat_id, {})
    idx = session.get("post_index", 0)

    if idx >= POSTS_PER_SESSION:
        await bot.send_message(
            chat_id=chat_id,
            text=f"✅ Sesiunea completa! {len(session.get('packages', []))} posturi aprobate.",
        )
        _sessions.pop(chat_id, None)
        return

    settings = get_settings()
    times = [settings.post_time_1, settings.post_time_2, settings.post_time_3]
    slot = times[idx] if idx < len(times) else "?"

    await bot.send_message(
        chat_id=chat_id,
        text=f"📋 *Post {idx + 1}/{POSTS_PER_SESSION}* — Slot: {slot}\n\nSelecteaza categoria:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_category_keyboard(),
    )


async def _handle_category(update: Update, context: ContextTypes.DEFAULT_TYPE, category_num: int):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    category_name = CATEGORY_NAMES[category_num]

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"*{category_name}*\n\nCate poze vrei in carusel?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_image_count_keyboard(category_num),
    )


async def _handle_image_count(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    category_num: int,
    image_count: int,
):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    category_name = CATEGORY_NAMES[category_num]

    blocking_message = _stock_blocking_message(image_count)
    if blocking_message:
        await context.bot.send_message(
            chat_id=chat_id,
            text=blocking_message,
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Construiesc post *{category_name}* cu *{image_count} poze*...",
        parse_mode=ParseMode.MARKDOWN,
    )

    def _build():
        from core.post_builder import PostBuilder
        return PostBuilder().build_post(
            category_name,
            image_count=image_count,
            allow_auto_scrape=False,
        )

    prepare_mode = _sessions.get(chat_id, {}).get("mode") == "prepare"
    try:
        pkg = await _run_in_thread(_build)
        kboard = _prepare_keyboard(pkg.post_id) if prepare_mode else None
        await _send_post_preview(context.bot, chat_id, pkg, keyboard=kboard)
    except Exception as exc:
        logger.exception("Build post failed")
        await context.bot.send_message(chat_id=chat_id, text=f"Eroare la construire post: {exc}")
        if chat_id in _sessions:
            _sessions[chat_id]["post_index"] += 1
            await _ask_category(context.bot, chat_id)

async def _handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: int):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    pkg = _pending.get(post_id)
    if not pkg:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    get_db().mark_post_status(post_id, "approved")
    await query.edit_message_text(
        "✅ *Aprobat! Pe ce platforme postam?*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_platform_keyboard(post_id),
    )


async def _handle_platform_select(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: int, platform: str):
    query = update.callback_query
    await query.answer(f"Se publica pe {platform}...")
    chat_id = query.message.chat_id

    pkg = _pending.pop(post_id, None)
    if not pkg:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    label = "toate platformele" if platform == "all" else platform.upper()
    await query.edit_message_text(f"⏳ *Se publica pe {label}...*", parse_mode=ParseMode.MARKDOWN)

    try:
        results = await _publish_package(pkg, platforms={platform})
        if not results:
            lines = ["*Post aprobat si pastrat in coada.*",
                     "Platforma selectata nu este activa in .env."]
        elif any(result.success for _, result in results):
            lines = [f"*Publicat pe {label}!*\n"]
            for plat, result in results:
                if result.success:
                    lines.append(f"{plat.upper()}: {_escape_md(str(result.url or result.platform_post_id or ''))}")
                else:
                    lines.append(f"{plat.upper()} failed: {_escape_md(str(result.error or '')[:80])}")
        else:
            lines = [f"*Publicarea pe {label} a esuat.*\n"]
            for plat, result in results:
                lines.append(f"{plat.upper()} failed: {_escape_md(str(result.error or '')[:80])}")
        await context.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:
        logger.exception("Publish failed")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Eroare publicare: {exc}")

    # Next post
    if chat_id in _sessions:
        _sessions[chat_id]["post_index"] += 1
        _sessions[chat_id]["packages"].append(pkg)
        await _ask_category(context.bot, chat_id)


async def _handle_reject(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: int):
    query = update.callback_query
    await query.answer("❌ Respins")
    chat_id = query.message.chat_id

    _pending.pop(post_id, None)
    get_db().mark_post_status(post_id, "rejected")
    await query.edit_message_text("❌ *Post respins.*", parse_mode=ParseMode.MARKDOWN)

    if chat_id in _sessions:
        _sessions[chat_id]["post_index"] += 1
        await _ask_category(context.bot, chat_id)


async def _handle_regen(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: int):
    query = update.callback_query
    await query.answer("🔄 Regenerez captioanele...")
    chat_id = query.message.chat_id

    pkg = _pending.get(post_id)
    if not pkg:
        return

    await query.edit_message_text("🔄 *Regenerez captioanele cu GPT-4o...*", parse_mode=ParseMode.MARKDOWN)

    def _regen():
        from captions.caption_generator import get_caption_generator
        from pathlib import Path
        cap_gen = get_caption_generator()
        new_caps = cap_gen.generate_all_platforms(
            Path(pkg.pinterest_image_path), pkg.products, pkg.category
        )
        new_fmt = {
            pl: cap_gen.format_for_platform(cd, pkg.products, pl)
            for pl, cd in new_caps.items()
        }
        return new_caps, new_fmt

    try:
        new_caps, new_fmt = await _run_in_thread(_regen)
        pkg.captions = new_caps
        pkg.formatted_captions = new_fmt
        get_db().update_post_captions(
            post_id,
            new_fmt.get("instagram", ""),
            " ".join(f"#{h}" for h in new_caps.get("instagram", {}).get("hashtags", [])),
            captions_json=new_caps,
            formatted_captions_json=new_fmt,
        )
        await context.bot.send_message(chat_id=chat_id, text="✅ Captioane regenerate!")
        await _send_post_preview(context.bot, chat_id, pkg)
    except Exception as exc:
        logger.exception("Regen failed")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Eroare regenerare: {exc}")


# ── Prepare-mode handlers ────────────────────────────────────────────────────

async def _handle_p_add(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: int):
    """Adauga post in coada scheduled (nu publica acum)."""
    query = update.callback_query
    await query.answer("📅 Adăugat în coadă!")
    chat_id = query.message.chat_id

    pkg = _pending.pop(post_id, None)
    if not pkg:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    get_db().mark_post_status(post_id, "scheduled")
    scheduled_count = len(get_db().get_posts_by_status("scheduled"))
    await query.edit_message_text(
        f"📅 *Post #{post_id} adăugat în coadă!*\nPosturi în coadă: {scheduled_count}",
        parse_mode=ParseMode.MARKDOWN,
    )

    if chat_id in _sessions:
        _sessions[chat_id]["post_index"] += 1
        _sessions[chat_id].setdefault("packages", []).append(pkg)
        idx = _sessions[chat_id]["post_index"]
        if idx >= POSTS_PER_SESSION:
            settings = get_settings()
            times = [settings.post_time_1, settings.post_time_2, settings.post_time_3]
            queued = get_db().get_posts_by_status("scheduled")
            lines = ["✅ *Pregătire completă!*\n", "Posturi în coadă:"]
            for i, rec in enumerate(queued[:3]):
                slot = times[i] if i < len(times) else "?"
                lines.append(f"  • Post #{rec['id']} → {slot}")
            _sessions.pop(chat_id, None)
            await context.bot.send_message(
                chat_id=chat_id,
                text="\n".join(lines),
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await _ask_category(context.bot, chat_id)


async def _handle_p_regen(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: int):
    """Regenereaza postul complet cu poze noi."""
    query = update.callback_query
    await query.answer("🔄 Regenerez cu poze noi...")
    chat_id = query.message.chat_id

    pkg = _pending.pop(post_id, None)
    if not pkg:
        return

    get_db().mark_post_status(post_id, "rejected")
    await query.edit_message_text("🔄 *Construiesc post nou cu poze diferite...*", parse_mode=ParseMode.MARKDOWN)

    category_name = pkg.category
    image_count = len(pkg.all_images)

    def _rebuild():
        from core.post_builder import PostBuilder
        return PostBuilder().build_post(category_name, image_count=image_count, allow_auto_scrape=False)

    try:
        new_pkg = await _run_in_thread(_rebuild)
        await _send_post_preview(context.bot, chat_id, new_pkg, keyboard=_prepare_keyboard(new_pkg.post_id))
    except Exception as exc:
        logger.exception("Regen post (poze noi) failed")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Eroare regenerare post: {exc}")


async def _handle_p_caps(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: int):
    """Regenereaza doar captioanele, pastreaza pozele."""
    query = update.callback_query
    await query.answer("✏️ Regenerez captioanele...")
    chat_id = query.message.chat_id

    pkg = _pending.get(post_id)
    if not pkg:
        return

    await query.edit_message_text("✏️ *Regenerez captioanele cu GPT-4o...*", parse_mode=ParseMode.MARKDOWN)

    def _regen():
        from captions.caption_generator import get_caption_generator
        cap_gen = get_caption_generator()
        new_caps = cap_gen.generate_all_platforms(
            Path(pkg.pinterest_image_path), pkg.products, pkg.category
        )
        new_fmt = {
            pl: cap_gen.format_for_platform(cd, pkg.products, pl)
            for pl, cd in new_caps.items()
        }
        return new_caps, new_fmt

    try:
        new_caps, new_fmt = await _run_in_thread(_regen)
        pkg.captions = new_caps
        pkg.formatted_captions = new_fmt
        get_db().update_post_captions(
            post_id,
            new_fmt.get("instagram", ""),
            " ".join(f"#{h}" for h in new_caps.get("instagram", {}).get("hashtags", [])),
            captions_json=new_caps,
            formatted_captions_json=new_fmt,
        )
        await context.bot.send_message(chat_id=chat_id, text="✅ Captioane regenerate!")
        await _send_post_preview(context.bot, chat_id, pkg, keyboard=_prepare_keyboard(pkg.post_id))
    except Exception as exc:
        logger.exception("Caps regen (prepare) failed")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Eroare regenerare captions: {exc}")


async def _handle_p_skip(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: int):
    """Skip post in modul prepare."""
    query = update.callback_query
    await query.answer("❌ Skip")
    chat_id = query.message.chat_id

    _pending.pop(post_id, None)
    get_db().mark_post_status(post_id, "rejected")
    await query.edit_message_text("❌ *Post sărit.*", parse_mode=ParseMode.MARKDOWN)

    if chat_id in _sessions:
        _sessions[chat_id]["post_index"] += 1
        await _ask_category(context.bot, chat_id)


# ── Master callback handler ───────────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    settings = get_settings()
    logger.info("Callback primit de la chat_id={}, asteptat={}", query.message.chat_id, settings.telegram_chat_id)
    if query.message.chat_id != settings.telegram_chat_id:
        logger.warning("Callback ignorat — chat_id nepotrivit")
        return

    data = query.data
    logger.info("Callback data: {}", data)
    try:
        if data.startswith("cat:"):
            import random as _random
            cat_num = int(data.split(":")[1])
            if cat_num == 0:
                cat_num = _random.choice(list(CATEGORY_NAMES.keys()))
            await _handle_category(update, context, cat_num)
        elif data.startswith("count:"):
            _, category_num, image_count = data.split(":")
            await _handle_image_count(
                update,
                context,
                int(category_num),
                int(image_count),
            )
        elif data.startswith("approve:"):
            await _handle_approve(update, context, int(data.split(":")[1]))
        elif data.startswith("pub:"):
            _, post_id, platform = data.split(":", 2)
            await _handle_platform_select(update, context, int(post_id), platform)
        elif data.startswith("reject:"):
            await _handle_reject(update, context, int(data.split(":")[1]))
        elif data.startswith("regen:"):
            await _handle_regen(update, context, int(data.split(":")[1]))
        elif data.startswith("p_add:"):
            await _handle_p_add(update, context, int(data.split(":")[1]))
        elif data.startswith("p_regen:"):
            await _handle_p_regen(update, context, int(data.split(":")[1]))
        elif data.startswith("p_caps:"):
            await _handle_p_caps(update, context, int(data.split(":")[1]))
        elif data.startswith("p_skip:"):
            await _handle_p_skip(update, context, int(data.split(":")[1]))
    except Exception as exc:
        logger.exception("Eroare in callback_handler pentru data={}: {}", data, exc)
        try:
            await query.answer(f"Eroare: {exc}", show_alert=True)
        except Exception:
            pass
        try:
            chat_id = query.message.chat_id
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Eroare buton: {exc}")
        except Exception:
            pass


# ── Scheduled job ─────────────────────────────────────────────────────────────

async def scheduled_post_job(bot: Bot, chat_id: int):
    """Declansat automat la orele programate — publica primul post din coada scheduled."""
    logger.info("Scheduled post job triggered")
    from core.post_builder import package_from_db_record

    scheduled = get_db().get_posts_by_status("scheduled")
    if not scheduled:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ *Ora de postare — coada e goală!*\n\n"
                "Nu sunt posturi pregătite. Rulează `.prepare` pentru a adăuga posturi în coadă."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    record = scheduled[0]
    post_id = record["id"]
    logger.info("Auto-posting scheduled post #{}", post_id)
    await bot.send_message(
        chat_id=chat_id,
        text=f"⏰ *Ora de postare!*\nPublic post #{post_id} din coadă...",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        pkg = package_from_db_record(record)
        results = await _publish_package(pkg)
        remaining = len(get_db().get_posts_by_status("scheduled"))

        if not results:
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Post #{post_id}: nicio platformă activă.\nPosturi rămase în coadă: {remaining}",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        lines = [f"✅ *Post #{post_id} publicat!*\n"]
        for platform, result in results:
            if result.success:
                lines.append(f"{platform.upper()}: {_escape_md(str(result.url or result.platform_post_id or ''))}")
            else:
                lines.append(f"{platform.upper()} FAILED: {_escape_md(str(result.error or '')[:80])}")
        lines.append(f"\n📋 Posturi rămase în coadă: {remaining}")
        if remaining == 0:
            lines.append("⚠️ Coada e goală! Rulează `.prepare` când ești gata.")
        await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    except Exception as exc:
        logger.exception("Scheduled post job failed for post {}", post_id)
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Eroare la publicare post #{post_id}: {_escape_md(str(exc)[:200])}",
            parse_mode=ParseMode.MARKDOWN,
        )


# ── App builder ───────────────────────────────────────────────────────────────

async def dot_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return
    text = (update.message.text or "").strip()
    parts = text[1:].split() if text.startswith(".") else []
    if not parts:
        return
    command = parts[0].lower()
    context.args = parts[1:]

    handlers = {
        "start": cmd_dot_start,
        "help": cmd_help,
        "status": cmd_status,
        "queue": cmd_queue,
        "platforms": cmd_platforms,
        "readiness": cmd_readiness,
        "doctor": cmd_readiness,
        "run": cmd_run,
        "prepare": cmd_prepare,
        "postqueue": cmd_postqueue,
        "scrape": cmd_scrape,
        "scrapeproducts": cmd_scrapeproducts,
        "syncsheet": cmd_syncsheet,
        "cacheimages": cmd_cacheimages,
        "resetpinterest": cmd_resetpinterest,
        "resetmulebuy": cmd_resetmulebuy,
        "resetall": cmd_resetall,
    }
    handler = handlers.get(command)
    if handler:
        await handler(update, context)
    else:
        await update.message.reply_text("Comanda necunoscuta. Scrie .help")


def build_application() -> Application:
    settings = get_settings()
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("platforms", cmd_platforms))
    app.add_handler(CommandHandler("readiness", cmd_readiness))
    app.add_handler(CommandHandler("doctor", cmd_readiness))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("prepare", cmd_prepare))
    app.add_handler(CommandHandler("postqueue", cmd_postqueue))
    app.add_handler(CommandHandler("scrape", cmd_scrape))
    app.add_handler(CommandHandler("scrapeproducts", cmd_scrapeproducts))
    app.add_handler(CommandHandler("syncsheet", cmd_syncsheet))
    app.add_handler(CommandHandler("cacheimages", cmd_cacheimages))
    app.add_handler(MessageHandler(filters.Regex(r"^\s*\.\w+"), dot_command_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))

    return app
