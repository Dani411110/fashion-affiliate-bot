"""Google Sheets client with in-memory caching."""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials

from utils.logger import get_logger
from utils.retry import retry_on_network_error

logger = get_logger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

CACHE_TTL = 30 * 60  # 30 minutes


class SheetError(Exception):
    pass


class SheetsClient:
    def __init__(self, service_account_json: str, sheet_id: str, tab_name: str):
        self._sheet_id = sheet_id
        self._tab_name = tab_name
        self._cache: Optional[List[Dict[str, Any]]] = None
        self._cache_time: float = 0.0
        self._client = self._build_client(service_account_json)
        logger.info("SheetsClient initialised for sheet {}", sheet_id)

    @staticmethod
    def _build_client(service_account_json: str) -> gspread.Client:
        try:
            if Path(service_account_json).exists():
                creds = Credentials.from_service_account_file(
                    service_account_json, scopes=_SCOPES
                )
            else:
                info = json.loads(service_account_json)
                creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
            return gspread.authorize(creds)
        except Exception as exc:
            raise SheetError(f"Failed to authenticate with Google Sheets: {exc}") from exc

    @retry_on_network_error
    def _fetch_raw(self) -> List[List[str]]:
        try:
            sheet = self._client.open_by_key(self._sheet_id)
            ws = sheet.worksheet(self._tab_name)
            return ws.get_all_values()
        except gspread.exceptions.APIError as exc:
            raise SheetError(f"Sheets API error: {exc}") from exc
        except Exception as exc:
            raise SheetError(f"Unexpected Sheets error: {exc}") from exc

    def get_all_products(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Return all product rows as a list of dicts.

        Uses a 30-minute in-memory cache. Pass force_refresh=True to bypass it.
        """
        now = time.monotonic()
        if not force_refresh and self._cache and (now - self._cache_time) < CACHE_TTL:
            logger.debug("Returning {} products from cache", len(self._cache))
            return self._cache

        logger.info("Fetching products from Google Sheet tab '{}'", self._tab_name)
        rows = self._fetch_raw()

        if len(rows) < 2:
            logger.warning("Sheet has no data rows.")
            self._cache = []
            self._cache_time = now
            return []

        products: List[Dict[str, Any]] = []
        for idx, row in enumerate(rows[1:], start=2):  # row 1 is header; idx = sheet row number
            if len(row) < 7:
                logger.debug("Skipping incomplete row {} (only {} cols)", idx, len(row))
                continue

            try:
                price = float(row[3].strip()) if row[3].strip() else 0.0
            except ValueError:
                price = 0.0

            try:
                popularity_score = int(row[6].strip()) if row[6].strip() else 0
            except ValueError:
                popularity_score = 0

            products.append(
                {
                    "sheet_row_index": idx,
                    "image_url": row[0].strip(),
                    "mulebuy_link": row[1].strip(),
                    "category": row[2].strip().lower(),
                    "price": price,
                    "name": row[4].strip(),
                    "tags": row[5].strip().lower(),
                    "popularity_score": popularity_score,
                }
            )

        logger.info("Fetched {} products from sheet", len(products))
        self._cache = products
        self._cache_time = now
        return products

    def invalidate_cache(self):
        self._cache = None
        self._cache_time = 0.0


_client_instance: Optional[SheetsClient] = None


def get_sheets_client() -> SheetsClient:
    global _client_instance
    if _client_instance is None:
        from config.settings import get_settings
        s = get_settings()
        _client_instance = SheetsClient(
            s.google_service_account_json,
            s.google_sheet_id,
            s.google_sheet_tab_name,
        )
    return _client_instance
