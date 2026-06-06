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
    buttons = [
        [InlineKeyboardButton(f"{k}. {v}", callback_data=f"cat:{k}")]
        for k, v in CATEGORY_NAMES.items()
    ]
    return InlineKeyboardMarkup(buttons)


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
        ".start - alege categoria si numarul de poze\n"
        ".status - statistici DB\n"
        ".queue - ultimele postari si coada\n"
        ".platforms - platforme active/inactive\n"
        ".scrape 50 - scrape Pinterest\n"
        ".scrapeproducts 30 - scrape Mulebuy\n"
        ".syncsheet - sync Google Sheet in SQLite\n"
        ".run - sesiune de 3 posturi\n\n"
        "Flow: .start -> categorie -> 5/6/7/8 poze -> preview album -> approve/reject/regenerate."
    )


def _platform_status_lines() -> list[str]:
    settings = get_settings()
    return [
        f"Reddit: {'ON' if settings.enable_reddit else 'OFF'}",
        f"Instagram: {'ON' if settings.enable_instagram else 'OFF'}",
        f"TikTok: {'ON' if settings.enable_tiktok else 'OFF'}",
        f"YouTube: {'ON' if settings.enable_youtube else 'OFF'}",
    ]


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
        caption = shorten((row.get("caption") or "").replace("\n", " "), width=70, placeholder="...")
        lines.append(
            f"#{row['id']} | {row['status']} | {row['category']} | {row.get('carousel_image_count', 0)} poze"
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


def _format_preview(pkg) -> str:
    lines = [
        f"*Post #{pkg.post_id}*",
        f"Categoria: {pkg.category}",
        f"Carusel: {len(pkg.all_images)} imagini",
        "",
        "*Produse:*",
    ]
    for idx, p in enumerate(pkg.products, start=1):
        name = shorten(str(p.get("name", "Product")), width=42, placeholder="...")
        price = float(p.get("price", 0) or 0)
        link = p.get("mulebuy_link", "")
        lines.append(f"{idx}. [{name}]({link}) - ${price:.2f}")

    reddit = pkg.formatted_captions.get("reddit", "")
    instagram = pkg.formatted_captions.get("instagram", "")
    tiktok = pkg.formatted_captions.get("tiktok", "")

    lines += ["", "*Reddit:*", shorten(reddit, width=700, placeholder="...")]
    lines += ["", "*TikTok:*", shorten(tiktok, width=500, placeholder="...")]
    lines += ["", "*Instagram:*", shorten(instagram, width=500, placeholder="...")]
    return "\n".join(lines)

async def _send_post_preview(bot: Bot, chat_id: int, pkg):
    """Trimite preview complet al postului cu butoane de aprobare."""
    _pending[pkg.post_id] = pkg

    image_paths = [Path(p) for p in pkg.all_images if p and Path(p).exists()]
    try:
        if len(image_paths) >= 2:
            files = []
            media = []
            try:
                for idx, path in enumerate(image_paths[:10], start=1):
                    f = open(path, "rb")
                    files.append(f)
                    media.append(
                        InputMediaPhoto(
                            media=f,
                            caption=f"Preview carusel ({len(image_paths)} poze)" if idx == 1 else None,
                        )
                    )
                await bot.send_media_group(chat_id=chat_id, media=media)
            finally:
                for f in files:
                    f.close()
        elif image_paths:
            with open(image_paths[0], "rb") as f:
                await bot.send_photo(chat_id=chat_id, photo=f, caption="Preview carusel")
    except Exception:
        logger.warning("Nu am putut trimite preview-ul complet de imagini")

    text = _format_preview(pkg)
    await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_approval_keyboard(pkg.post_id),
    )

async def _publish_package(pkg):
    """Publica postul pe toate platformele activate."""
    from config.settings import get_settings
    settings = get_settings()
    results = []

    def _do_publish():
        publishers = []
        if settings.enable_reddit:
            from publishers.reddit_publisher import RedditPublisher
            publishers.append(RedditPublisher(
                settings.reddit_client_id, settings.reddit_client_secret,
                settings.reddit_username, settings.reddit_password,
                settings.reddit_user_agent, settings.reddit_subreddit,
            ))
        if settings.enable_instagram:
            from publishers.instagram_publisher import InstagramPublisher
            publishers.append(InstagramPublisher(
                settings.instagram_access_token, settings.instagram_user_id
            ))
        if settings.enable_tiktok:
            from publishers.tiktok_publisher import TikTokPublisher
            publishers.append(TikTokPublisher(
                settings.tiktok_cookies_path, settings.tiktok_access_token
            ))
        if settings.enable_youtube:
            from publishers.youtube_publisher import YouTubePublisher
            publishers.append(YouTubePublisher(
                settings.youtube_client_secrets_json,
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


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Porneste o sesiune manuala de 3 posturi."""
    settings = get_settings()
    if update.effective_chat.id != settings.telegram_chat_id:
        return

    chat_id = update.effective_chat.id
    _sessions[chat_id] = {
        "post_index": 0,
        "packages": [],
    }
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
                    lines.append(f"{platform.upper()}: {result.url or result.platform_post_id}")
                else:
                    lines.append(f"{platform.upper()} failed: {result.error[:100]}")
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
    category_name = CATEGORY_NAMES[category_num]

    await query.edit_message_text(
        f"*{category_name}*\n\nCate poze vrei in carusel?",
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
        await query.edit_message_text(
            blocking_message,
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await query.edit_message_text(
        f"Construiesc post *{category_name}* cu *{image_count} poze*...",
        parse_mode=ParseMode.MARKDOWN,
    )

    def _build():
        from core.post_builder import PostBuilder
        return PostBuilder().build_post(
            category_name,
            image_count=image_count,
            allow_auto_scrape=False,
        )

    try:
        pkg = await _run_in_thread(_build)
        await _send_post_preview(context.bot, chat_id, pkg)
    except Exception as exc:
        logger.exception("Build post failed")
        await context.bot.send_message(chat_id=chat_id, text=f"Eroare la construire post: {exc}")
        if chat_id in _sessions:
            _sessions[chat_id]["post_index"] += 1
            await _ask_category(context.bot, chat_id)

async def _handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: int):
    query = update.callback_query
    await query.answer("✅ Aprobat! Se publica...")
    chat_id = query.message.chat_id

    pkg = _pending.pop(post_id, None)
    if not pkg:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    get_db().mark_post_status(post_id, "approved")
    await query.edit_message_text("✅ *Aprobat! Se publica...*", parse_mode=ParseMode.MARKDOWN)

    try:
        results = await _publish_package(pkg)
        if not results:
            lines = [
                "*Post aprobat si pastrat in coada.*",
                "Nicio platforma nu este activa acum. Activeaza o platforma in .env/Railway, apoi publica.",
            ]
        elif any(result.success for _, result in results):
            lines = ["*Publicat pe platformele disponibile!*\n"]
            for platform, result in results:
                if result.success:
                    lines.append(f"{platform.upper()}: {result.url or result.platform_post_id}")
                else:
                    lines.append(f"{platform.upper()} failed: {result.error[:80]}")
        else:
            lines = ["*Post aprobat, dar publicarea a esuat pe toate platformele.*\n"]
            for platform, result in results:
                lines.append(f"{platform.upper()} failed: {result.error[:80]}")
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
            new_fmt.get("reddit", ""),
            " ".join(f"#{h}" for h in new_caps.get("reddit", {}).get("hashtags", [])),
            captions_json=new_caps,
            formatted_captions_json=new_fmt,
        )
        await context.bot.send_message(chat_id=chat_id, text="✅ Captioane regenerate!")
        await _send_post_preview(context.bot, chat_id, pkg)
    except Exception as exc:
        logger.exception("Regen failed")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Eroare regenerare: {exc}")


# ── Master callback handler ───────────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    settings = get_settings()
    if query.message.chat_id != settings.telegram_chat_id:
        return

    data = query.data
    if data.startswith("cat:"):
        await _handle_category(update, context, int(data.split(":")[1]))
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
    elif data.startswith("reject:"):
        await _handle_reject(update, context, int(data.split(":")[1]))
    elif data.startswith("regen:"):
        await _handle_regen(update, context, int(data.split(":")[1]))


# ── Scheduled job ─────────────────────────────────────────────────────────────

async def scheduled_post_job(bot: Bot, chat_id: int):
    """Declansat automat la orele programate."""
    logger.info("Scheduled post job triggered")
    _sessions[chat_id] = {"post_index": 0, "packages": []}
    await bot.send_message(
        chat_id=chat_id,
        text="⏰ *Ora de postare!*\nSelecteaza categoria pentru primul post:",
        parse_mode=ParseMode.MARKDOWN,
    )
    await _ask_category(bot, chat_id)


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
        "run": cmd_run,
        "postqueue": cmd_postqueue,
        "scrape": cmd_scrape,
        "scrapeproducts": cmd_scrapeproducts,
        "syncsheet": cmd_syncsheet,
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
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("postqueue", cmd_postqueue))
    app.add_handler(CommandHandler("scrape", cmd_scrape))
    app.add_handler(CommandHandler("scrapeproducts", cmd_scrapeproducts))
    app.add_handler(CommandHandler("syncsheet", cmd_syncsheet))
    app.add_handler(MessageHandler(filters.Regex(r"^\s*\.\w+"), dot_command_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))

    return app
