"""
Entry point pentru Railway / server cloud.

Porneste:
1. APScheduler — triggereaza postari la 9:00, 14:00, 19:00
2. Telegram Bot — polling pentru comenzi si aprobare

Ruleaza continuu, nu se opreste niciodata.
"""

import asyncio
import logging
import os

# ── ffmpeg PATH fix (Windows: winget installs to AppData/Local/Microsoft/WinGet/Links) ──
def _ensure_ffmpeg_in_path():
    import subprocess
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return  # already in PATH
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    # Common Windows locations
    import pathlib
    candidates = [
        pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",
        pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
        pathlib.Path("C:/ProgramData/chocolatey/bin"),
        pathlib.Path("C:/ffmpeg/bin"),
        pathlib.Path("C:/Program Files/ffmpeg/bin"),
    ]
    for c in candidates:
        if c.exists() and any(c.glob("ffmpeg*")):
            os.environ["PATH"] = str(c) + os.pathsep + os.environ.get("PATH", "")
            return

_ensure_ffmpeg_in_path()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config.settings import get_settings
from core.debug_server import start_debug_server
from core.telegram_bot import build_application, scheduled_post_job
from utils.logger import get_logger

logger = get_logger(__name__)


def _parse_time(t: str):
    """Parses 'HH:MM' → (hour, minute)."""
    parts = t.split(":")
    return int(parts[0]), int(parts[1])


async def main():
    settings = get_settings()
    logger.info("Starting Fashion Affiliate Bot server...")
    logger.info("Telegram chat_id: {}", settings.telegram_chat_id)
    logger.info("SQLite path: {}", settings.sqlite_path)
    logger.info(
        "Railway volume: {}",
        os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "not detected"),
    )
    logger.info(
        "Platforms enabled: instagram={}, tiktok={}, youtube={}",
        settings.enable_instagram,
        settings.enable_tiktok,
        settings.enable_youtube,
    )
    debug_server = start_debug_server(settings)

    app = build_application()
    bot = app.bot

    # APScheduler
    scheduler = AsyncIOScheduler(timezone="Europe/Bucharest")

    for slot in [settings.post_time_1, settings.post_time_2, settings.post_time_3]:
        hour, minute = _parse_time(slot)
        scheduler.add_job(
            scheduled_post_job,
            CronTrigger(hour=hour, minute=minute),
            args=[bot, settings.telegram_chat_id],
            id=f"post_{slot.replace(':', '')}",
            replace_existing=True,
        )
        logger.info("Scheduled posting job at {}:{:02d}", hour, minute)

    scheduler.start()
    logger.info("APScheduler started with {} jobs", len(scheduler.get_jobs()))

    # Trimite mesaj de startup
    try:
        await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=(
                "*Fashion Bot pornit pe server!*\n\n"
                f"Posturi programate: {settings.post_time_1}, "
                f"{settings.post_time_2}, {settings.post_time_3}\n\n"
                "Scrie .status, .platforms sau .start pentru a incepe manual."
            ),            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.warning("Nu am putut trimite mesajul de startup: {}", exc)

    # Porneste polling Telegram
    logger.info("Starting Telegram bot polling...")
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        # Tine botul pornit
        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            scheduler.shutdown()
            if debug_server:
                debug_server.shutdown()
            await app.updater.stop()
            await app.stop()
            await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
