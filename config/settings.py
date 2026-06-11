"""
Application settings loaded from environment variables.

# PHASE 2 MIGRATION:
# Replace Settings with a Pydantic BaseSettings model backed by a remote
# config store (e.g. Supabase secrets or a Telegram-bot config command).
# All field names and types stay the same — only the source changes.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / ".env")


def _require(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise EnvironmentError(f"Required environment variable '{key}' is not set.")
    return val


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _bool(key: str, default: bool = True) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes")


def _int(key: str, default: int = 0) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float = 0.0) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _path(key: str, default: str) -> Path:
    raw = _get(key, default)
    path = Path(raw)
    return path if path.is_absolute() else _ROOT / path


def _default_sqlite_path() -> str:
    railway_volume = _get("RAILWAY_VOLUME_MOUNT_PATH")
    if railway_volume:
        return str(Path(railway_volume) / "fashion_bot.db")
    return "data/fashion_bot.db"


def _default_youtube_token_path() -> str:
    railway_volume = _get("RAILWAY_VOLUME_MOUNT_PATH")
    if railway_volume:
        return str(Path(railway_volume) / "youtube_token.json")
    return "data/youtube_token.json"


def _default_temp_folder() -> str:
    railway_volume = _get("RAILWAY_VOLUME_MOUNT_PATH")
    if railway_volume:
        return str(Path(railway_volume) / "temp")
    return "data/temp"


def _default_product_images_folder() -> str:
    railway_volume = _get("RAILWAY_VOLUME_MOUNT_PATH")
    if railway_volume:
        return str(Path(railway_volume) / "product_images")
    return "data/product_images"


@dataclass
class Settings:
    # ── OpenAI ───────────────────────────────────────────────────────────
    openai_api_key: str = field(default_factory=lambda: _require("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _get("OPENAI_MODEL", "gpt-4o"))

    # ── Google ───────────────────────────────────────────────────────────
    google_service_account_json: str = field(
        default_factory=lambda: _require("GOOGLE_SERVICE_ACCOUNT_JSON")
    )
    google_sheet_id: str = field(default_factory=lambda: _require("GOOGLE_SHEET_ID"))
    google_sheet_tab_name: str = field(
        default_factory=lambda: _get("GOOGLE_SHEET_TAB_NAME", "Products")
    )

    # ── Google Drive folder IDs ───────────────────────────────────────────
    drive_folder_queue_id: str = field(
        default_factory=lambda: _get("DRIVE_FOLDER_QUEUE_ID")
    )
    drive_folder_posted_id: str = field(
        default_factory=lambda: _get("DRIVE_FOLDER_POSTED_ID")
    )
    drive_folder_rejected_id: str = field(
        default_factory=lambda: _get("DRIVE_FOLDER_REJECTED_ID")
    )
    drive_folder_raw_pinterest_id: str = field(
        default_factory=lambda: _get("DRIVE_FOLDER_RAW_PINTEREST_ID")
    )

    # ── Reddit ────────────────────────────────────────────────────────────
    reddit_client_id: str = field(default_factory=lambda: _get("REDDIT_CLIENT_ID"))
    reddit_client_secret: str = field(
        default_factory=lambda: _get("REDDIT_CLIENT_SECRET")
    )
    reddit_username: str = field(default_factory=lambda: _get("REDDIT_USERNAME"))
    reddit_password: str = field(default_factory=lambda: _get("REDDIT_PASSWORD"))
    reddit_user_agent: str = field(
        default_factory=lambda: _get(
            "REDDIT_USER_AGENT", "FashionAffiliateBot/1.0 by u/youruser"
        )
    )
    reddit_subreddit: str = field(
        default_factory=lambda: _get("REDDIT_SUBREDDIT", "fashionfinds")
    )

    # ── Instagram ────────────────────────────────────────────────────────
    instagram_access_token: str = field(
        default_factory=lambda: _get("INSTAGRAM_ACCESS_TOKEN")
    )
    instagram_user_id: str = field(
        default_factory=lambda: _get("INSTAGRAM_USER_ID")
    )

    # ── YouTube ──────────────────────────────────────────────────────────
    youtube_client_secrets_json: str = field(
        default_factory=lambda: _get("YOUTUBE_CLIENT_SECRETS_JSON")
    )
    youtube_token_json: str = field(
        default_factory=lambda: _get("YOUTUBE_TOKEN_JSON", "")
    )
    youtube_token_path: str = field(
        default_factory=lambda: _get("YOUTUBE_TOKEN_PATH", _default_youtube_token_path())
    )

    # ── TikTok ───────────────────────────────────────────────────────────
    tiktok_cookies_path: str = field(
        default_factory=lambda: _get(
            "TIKTOK_COOKIES_PATH", "data/tiktok_cookies.json"
        )
    )
    tiktok_client_key: str = field(
        default_factory=lambda: _get("TIKTOK_CLIENT_KEY", "")
    )
    tiktok_client_secret: str = field(
        default_factory=lambda: _get("TIKTOK_CLIENT_SECRET", "")
    )
    tiktok_redirect_uri: str = field(
        default_factory=lambda: _get(
            "TIKTOK_REDIRECT_URI",
            "https://fashion-affiliate-bot-production.up.railway.app/tiktok/callback",
        )
    )
    # Optional: TikTok Content Posting API OAuth token (for Strategy 1)
    # Get from https://developers.tiktok.com/ — needed for photo carousel API
    tiktok_access_token: str = field(
        default_factory=lambda: _get("TIKTOK_ACCESS_TOKEN", "")
    )
    tiktok_refresh_token: str = field(
        default_factory=lambda: _get("TIKTOK_REFRESH_TOKEN", "")
    )

    # ── Pixabay ──────────────────────────────────────────────────────────
    pixabay_api_key: str = field(default_factory=lambda: _get("PIXABAY_API_KEY"))

    # ── Telegram ─────────────────────────────────────────────────────────
    telegram_bot_token: str = field(default_factory=lambda: _get("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: int = field(default_factory=lambda: _int("TELEGRAM_CHAT_ID"))

    # ── Scheduling ───────────────────────────────────────────────────────
    post_time_1: str = field(default_factory=lambda: _get("POST_TIME_1", "09:00"))
    post_time_2: str = field(default_factory=lambda: _get("POST_TIME_2", "14:00"))
    post_time_3: str = field(default_factory=lambda: _get("POST_TIME_3", "19:00"))

    # ── Platform toggles ─────────────────────────────────────────────────
    enable_reddit: bool = field(default_factory=lambda: _bool("ENABLE_REDDIT", False))
    enable_tiktok: bool = field(default_factory=lambda: _bool("ENABLE_TIKTOK", False))
    enable_instagram: bool = field(
        default_factory=lambda: _bool("ENABLE_INSTAGRAM", False)
    )
    enable_youtube: bool = field(
        default_factory=lambda: _bool("ENABLE_YOUTUBE", False)
    )

    # ── Content parameters ────────────────────────────────────────────────
    max_products_per_post: int = field(
        default_factory=lambda: _int("MAX_PRODUCTS_PER_POST", 7)
    )
    min_products_per_post: int = field(
        default_factory=lambda: _int("MIN_PRODUCTS_PER_POST", 4)
    )
    video_seconds_per_image: float = field(
        default_factory=lambda: _float("VIDEO_SECONDS_PER_IMAGE", 3.0)
    )
    min_pinterest_stock: int = field(
        default_factory=lambda: _int("MIN_PINTEREST_STOCK", 5)
    )

    # ── Pinterest ─────────────────────────────────────────────────────────
    # Optional HTTP/SOCKS5 proxy for Pinterest scraper (Railway IPs often blocked)
    # Example: "http://user:pass@proxy.host:8080" or "socks5://proxy.host:1080"
    pinterest_proxy: str = field(
        default_factory=lambda: _get("PINTEREST_PROXY", "")
    )

    # ── Pinterest keywords ────────────────────────────────────────────────
    pinterest_keywords: List[str] = field(
        default_factory=lambda: [
            kw.strip()
            for kw in _get(
                "PINTEREST_KEYWORDS",
                (
                    "streetwear outfit,aesthetic fashion,korean fashion,y2k outfit,"
                    "coquette style,dark academia outfit,old money outfit,"
                    "clean girl outfit,airport outfit,casual street style,"
                    "minimal outfit,winter streetwear,summer outfit inspo"
                ),
            ).split(",")
            if kw.strip()
        ]
    )

    # ── Paths ─────────────────────────────────────────────────────────────
    sqlite_path: Path = field(
        default_factory=lambda: _path("SQLITE_PATH", _default_sqlite_path())
    )
    music_folder: Path = field(
        default_factory=lambda: _path("MUSIC_FOLDER", "data/music")
    )
    temp_folder: Path = field(
        default_factory=lambda: _path("TEMP_FOLDER", _default_temp_folder())
    )
    product_images_folder: Path = field(
        default_factory=lambda: _path("PRODUCT_IMAGES_FOLDER", _default_product_images_folder())
    )

    def __post_init__(self):
        self.sqlite_path = Path(self.sqlite_path)
        self.music_folder = Path(self.music_folder)
        self.temp_folder = Path(self.temp_folder)
        self.product_images_folder = Path(self.product_images_folder)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.music_folder.mkdir(parents=True, exist_ok=True)
        self.temp_folder.mkdir(parents=True, exist_ok=True)
        self.product_images_folder.mkdir(parents=True, exist_ok=True)


_instance: Settings | None = None


def get_settings() -> Settings:
    global _instance
    if _instance is None:
        _instance = Settings()
    return _instance
