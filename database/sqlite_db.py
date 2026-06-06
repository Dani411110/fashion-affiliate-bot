"""
SQLite database layer for the fashion affiliate bot.

# PHASE 2 MIGRATION:
# Extract a BaseDatabase abstract class with the same public interface.
# Replace SqliteDatabase with SupabaseDatabase that wraps the supabase-py client.
# All callers import get_db() which returns whichever implementation is configured.
# SQLite stays as the local/dev backend; Supabase handles production.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class SqliteDatabase:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path), detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS pinterest_images (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    url         TEXT    NOT NULL UNIQUE,
                    local_path  TEXT,
                    drive_path  TEXT,
                    image_hash  TEXT,
                    scraped_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                    used        INTEGER NOT NULL DEFAULT 0,
                    used_at     TEXT
                );

                CREATE TABLE IF NOT EXISTS products_cache (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    sheet_row_index     INTEGER NOT NULL UNIQUE,
                    name                TEXT    NOT NULL,
                    image_url           TEXT    NOT NULL,
                    mulebuy_link        TEXT    NOT NULL,
                    category            TEXT    NOT NULL,
                    price               REAL    NOT NULL DEFAULT 0,
                    tags                TEXT    NOT NULL DEFAULT '',
                    popularity_score    INTEGER NOT NULL DEFAULT 0,
                    last_synced         TEXT    NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS posts (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    category            TEXT    NOT NULL,
                    pinterest_image_url TEXT,
                    product_ids         TEXT    NOT NULL DEFAULT '[]',
                    caption             TEXT,
                    hashtags            TEXT,
                    video_path          TEXT,
                    drive_folder_id     TEXT,
                    status              TEXT    NOT NULL DEFAULT 'draft',
                    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
                    approved_at         TEXT,
                    posted_at           TEXT
                );

                CREATE TABLE IF NOT EXISTS post_platforms (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id             INTEGER NOT NULL REFERENCES posts(id),
                    platform            TEXT    NOT NULL,
                    status              TEXT    NOT NULL DEFAULT 'pending',
                    platform_post_id    TEXT,
                    posted_at           TEXT,
                    error_message       TEXT
                );

                CREATE TABLE IF NOT EXISTS used_products (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id     INTEGER NOT NULL REFERENCES posts(id),
                    product_sheet_row INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS music_usage (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_path  TEXT NOT NULL,
                    used_at     TEXT NOT NULL DEFAULT (datetime('now'))
                );
            """)
        logger.debug("SQLite schema initialised at {}", self.db_path)

    # ── Pinterest images ──────────────────────────────────────────────────

    def insert_pinterest_image(
        self,
        url: str,
        local_path: str,
        drive_path: str,
        image_hash: str,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO pinterest_images
                   (url, local_path, drive_path, image_hash)
                   VALUES (?,?,?,?)""",
                (url, local_path, drive_path, image_hash),
            )
            return cur.lastrowid or 0

    def is_duplicate_image(self, url: str, image_hash: str, threshold: int = 10) -> bool:
        with self._connect() as conn:
            if conn.execute(
                "SELECT 1 FROM pinterest_images WHERE url=?", (url,)
            ).fetchone():
                return True
            rows = conn.execute(
                "SELECT image_hash FROM pinterest_images WHERE image_hash IS NOT NULL"
            ).fetchall()
        try:
            import imagehash
            new_h = imagehash.hex_to_hash(image_hash)
            for row in rows:
                existing_h = imagehash.hex_to_hash(row["image_hash"])
                if (new_h - existing_h) <= threshold:
                    return True
        except Exception:
            logger.warning("Hash comparison failed — treating as non-duplicate")
        return False

    def get_unused_pinterest_images(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pinterest_images WHERE used=0 ORDER BY scraped_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_pinterest_image_used(self, image_id: int):
        with self._connect() as conn:
            conn.execute(
                "UPDATE pinterest_images SET used=1, used_at=? WHERE id=?",
                (datetime.utcnow().isoformat(), image_id),
            )

    def count_unused_pinterest_images(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM pinterest_images WHERE used=0"
            ).fetchone()
        return row["cnt"]

    # ── Products cache ────────────────────────────────────────────────────

    def sync_products(self, products: List[Dict[str, Any]]):
        """Upsert all products from the sheet into local cache."""
        with self._connect() as conn:
            for p in products:
                conn.execute(
                    """INSERT INTO products_cache
                       (sheet_row_index, name, image_url, mulebuy_link, category,
                        price, tags, popularity_score, last_synced)
                       VALUES (?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(sheet_row_index) DO UPDATE SET
                           name=excluded.name,
                           image_url=excluded.image_url,
                           mulebuy_link=excluded.mulebuy_link,
                           category=excluded.category,
                           price=excluded.price,
                           tags=excluded.tags,
                           popularity_score=excluded.popularity_score,
                           last_synced=excluded.last_synced""",
                    (
                        p["sheet_row_index"],
                        p["name"],
                        p["image_url"],
                        p["mulebuy_link"],
                        p["category"],
                        float(p.get("price", 0)),
                        p.get("tags", ""),
                        int(p.get("popularity_score", 0)),
                        datetime.utcnow().isoformat(),
                    ),
                )
        logger.info("Synced {} products to local cache", len(products))

    def get_all_cached_products(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM products_cache").fetchall()
        return [dict(r) for r in rows]

    def get_recently_used_product_rows(self, last_n_posts: int = 10) -> List[int]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT DISTINCT up.product_sheet_row
                   FROM used_products up
                   JOIN posts p ON p.id = up.post_id
                   ORDER BY p.created_at DESC
                   LIMIT ?""",
                (last_n_posts * 7,),
            ).fetchall()
        return [r["product_sheet_row"] for r in rows]

    # ── Posts ─────────────────────────────────────────────────────────────

    def create_post(
        self,
        category: str,
        pinterest_image_url: str,
        product_ids: List[int],
        caption: str,
        hashtags: str,
        video_path: str,
        drive_folder_id: str,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO posts
                   (category, pinterest_image_url, product_ids, caption, hashtags,
                    video_path, drive_folder_id, status)
                   VALUES (?,?,?,?,?,?,?,'draft')""",
                (
                    category,
                    pinterest_image_url,
                    json.dumps(product_ids),
                    caption,
                    hashtags,
                    video_path,
                    drive_folder_id,
                ),
            )
            return cur.lastrowid

    def get_post(self, post_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM posts WHERE id=?", (post_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_posts_by_status(self, status: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM posts WHERE status=? ORDER BY created_at ASC",
                (status,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_post_status(self, post_id: int, status: str):
        now = datetime.utcnow().isoformat()
        extra: Dict[str, str] = {}
        if status == "approved":
            extra["approved_at"] = now
        elif status == "posted":
            extra["posted_at"] = now

        set_clause = "status=?"
        params: list = [status]
        for col, val in extra.items():
            set_clause += f", {col}=?"
            params.append(val)
        params.append(post_id)

        with self._connect() as conn:
            conn.execute(
                f"UPDATE posts SET {set_clause} WHERE id=?", params
            )

    def update_post_captions(self, post_id: int, caption: str, hashtags: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE posts SET caption=?, hashtags=? WHERE id=?",
                (caption, hashtags, post_id),
            )

    def record_used_products(self, post_id: int, sheet_rows: List[int]):
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO used_products (post_id, product_sheet_row) VALUES (?,?)",
                [(post_id, r) for r in sheet_rows],
            )

    # ── Post platforms ────────────────────────────────────────────────────

    def upsert_platform_status(
        self,
        post_id: int,
        platform: str,
        status: str,
        platform_post_id: str = "",
        error_message: str = "",
    ):
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM post_platforms WHERE post_id=? AND platform=?",
                (post_id, platform),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE post_platforms
                       SET status=?, platform_post_id=?, posted_at=?, error_message=?
                       WHERE post_id=? AND platform=?""",
                    (status, platform_post_id, now, error_message, post_id, platform),
                )
            else:
                conn.execute(
                    """INSERT INTO post_platforms
                       (post_id, platform, status, platform_post_id, posted_at, error_message)
                       VALUES (?,?,?,?,?,?)""",
                    (post_id, platform, status, platform_post_id, now, error_message),
                )

    # ── Music usage ───────────────────────────────────────────────────────

    def record_music_usage(self, track_path: str):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO music_usage (track_path) VALUES (?)", (track_path,)
            )

    def get_recently_used_tracks(self, last_n: int = 10) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT track_path FROM music_usage ORDER BY used_at DESC LIMIT ?",
                (last_n,),
            ).fetchall()
        return [r["track_path"] for r in rows]

    # ── Stats ─────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            posts_total = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            posts_by_status = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT status, COUNT(*) FROM posts GROUP BY status"
                ).fetchall()
            }
            pinterest_total = conn.execute(
                "SELECT COUNT(*) FROM pinterest_images"
            ).fetchone()[0]
            pinterest_unused = conn.execute(
                "SELECT COUNT(*) FROM pinterest_images WHERE used=0"
            ).fetchone()[0]
            products_cached = conn.execute(
                "SELECT COUNT(*) FROM products_cache"
            ).fetchone()[0]
        return {
            "posts_total": posts_total,
            "posts_by_status": posts_by_status,
            "pinterest_total": pinterest_total,
            "pinterest_unused": pinterest_unused,
            "products_cached": products_cached,
        }


_db_instance: Optional[SqliteDatabase] = None


def get_db() -> SqliteDatabase:
    global _db_instance
    if _db_instance is None:
        from config.settings import get_settings
        _db_instance = SqliteDatabase(get_settings().sqlite_path)
    return _db_instance
