"""Centralized logging configuration using loguru."""

import sys
from pathlib import Path
from datetime import date
from loguru import logger as _logger

_INITIALIZED = False


def _initialize():
    global _INITIALIZED
    if _INITIALIZED:
        return

    _logger.remove()

    # Fix Windows console encoding for Unicode characters
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    _logger.add(
        sys.stdout,
        level="INFO",
        colorize=True,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
        ),
    )

    log_dir = Path(__file__).resolve().parents[1] / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"fashion_bot_{date.today()}.log"

    _logger.add(
        str(log_file),
        level="DEBUG",
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{line} — {message}",
        backtrace=True,
        diagnose=True,
    )

    _INITIALIZED = True


def get_logger(name: str):
    """Return a loguru logger bound to the given module name."""
    _initialize()
    return _logger.bind(name=name)
