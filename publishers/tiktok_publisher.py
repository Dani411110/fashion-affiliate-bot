"""TikTok photo carousel publisher.

Strategy (in order):
  1. TikTok Content Posting API v2 — direct HTTP, requires OAuth token.
     Set TIKTOK_ACCESS_TOKEN in .env if you have API access.
  2. Browser-based upload via Playwright using saved cookies — posts a
     photo carousel by automating the TikTok web upload UI.
  3. If both fail: log error, mark failed, continue (never crash the bot).
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Any, List, Optional

import requests

from publishers.base_publisher import BasePublisher, PublishResult
from utils.logger import get_logger

logger = get_logger(__name__)

_TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"
_UPLOAD_PAGE = "https://www.tiktok.com/upload?lang=en"


class TikTokPublisher(BasePublisher):
    platform_name = "tiktok"

    def __init__(self, cookies_path: str, access_token: str = ""):
        self._cookies_path = Path(cookies_path)
        self._access_token = access_token   # optional TikTok Content API token
        logger.info("TikTokPublisher initialised (carousel mode)")

    # ── Strategy 1: TikTok Content Posting API ────────────────────────────

    def _post_via_api(
        self, public_image_urls: List[str], caption: str
    ) -> Optional[PublishResult]:
        """Use TikTok Content Posting API v2 (requires access_token)."""
        if not self._access_token:
            return None

        try:
            headers = {
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json; charset=utf-8",
            }
            payload = {
                "post_info": {
                    "title": caption[:150],
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                },
                "source_info": {
                    "source": "PULL_FROM_URL",
                    "photo_cover_index": 0,
                    "photo_images": public_image_urls[:35],  # API max 35 images
                },
                "media_type": "PHOTO",
                "post_mode": "DIRECT_POST",
            }
            resp = requests.post(
                f"{_TIKTOK_API_BASE}/post/publish/content/init/",
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            publish_id = data.get("data", {}).get("publish_id", "")
            if publish_id:
                logger.info("TikTok photo carousel submitted via API, publish_id={}", publish_id)
                return PublishResult(
                    success=True,
                    platform_post_id=publish_id,
                    url="https://www.tiktok.com/",
                )
            logger.warning("TikTok API returned no publish_id: {}", data)
            return None
        except Exception as exc:
            logger.warning("TikTok API strategy failed: {}", exc)
            return None

    # ── Strategy 2: Browser automation via Playwright ─────────────────────

    async def _post_via_browser_async(
        self, image_paths: List[Path], caption: str
    ) -> Optional[PublishResult]:
        """Automate the TikTok web upload UI using saved cookies."""
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout

        if not self._cookies_path.exists():
            logger.warning("TikTok cookies not found at {}", self._cookies_path)
            return None

        try:
            cookies_raw = json.loads(self._cookies_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load TikTok cookies: {}", exc)
            return None

        # Normalise cookie format (from browser extensions vary)
        def _normalise(c: dict) -> dict:
            return {
                "name": c.get("name", c.get("Name", "")),
                "value": c.get("value", c.get("Value", "")),
                "domain": c.get("domain", ".tiktok.com"),
                "path": c.get("path", "/"),
                "secure": c.get("secure", True),
                "httpOnly": c.get("httpOnly", False),
                "sameSite": c.get("sameSite", "Lax"),
            }

        cookies = [_normalise(c) for c in cookies_raw if c.get("name") or c.get("Name")]

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            await context.add_cookies(cookies)
            page = await context.new_page()

            try:
                await page.goto(_UPLOAD_PAGE, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(3)

                # Detect login wall
                if "login" in page.url or await page.query_selector("input[name='username']"):
                    logger.error("TikTok browser: login wall — cookies may be expired")
                    return None

                # Switch to photo mode if toggle is visible
                try:
                    photo_toggle = await page.wait_for_selector(
                        "button:has-text('Photo'), [data-e2e='photo-tab']",
                        timeout=5_000,
                    )
                    if photo_toggle:
                        await photo_toggle.click()
                        await asyncio.sleep(1)
                except PWTimeout:
                    pass  # May already be in photo mode or UI differs

                # Upload images
                file_input = await page.wait_for_selector(
                    "input[type='file']", timeout=15_000
                )
                valid_paths = [str(p) for p in image_paths if Path(p).exists()]
                if not valid_paths:
                    return None

                await file_input.set_input_files(valid_paths[:35])
                await asyncio.sleep(5)   # wait for previews to load

                # Fill caption
                try:
                    caption_box = await page.wait_for_selector(
                        "[data-e2e='video-desc'], .public-DraftEditor-content, [contenteditable='true']",
                        timeout=10_000,
                    )
                    await caption_box.click()
                    await caption_box.fill(caption[:150])
                    await asyncio.sleep(0.5)
                except PWTimeout:
                    logger.warning("TikTok: could not locate caption box")

                # Click Post button
                post_btn = await page.wait_for_selector(
                    "button:has-text('Post'), [data-e2e='post-button']",
                    timeout=10_000,
                )
                await post_btn.click()
                await asyncio.sleep(8)   # wait for upload to complete

                # Check for success indicators
                success = False
                try:
                    await page.wait_for_selector(
                        ":has-text('successfully'), [data-e2e='upload-success']",
                        timeout=20_000,
                    )
                    success = True
                except PWTimeout:
                    # Some regions show different success UIs
                    current_url = page.url
                    if "upload" not in current_url:
                        success = True   # navigated away = likely success

                if success:
                    logger.info("TikTok photo carousel uploaded via browser automation")
                    return PublishResult(
                        success=True,
                        platform_post_id="browser_upload",
                        url="https://www.tiktok.com/",
                    )
                logger.error("TikTok browser upload: post button clicked but no success signal")
                return None

            except Exception:
                logger.exception("TikTok browser automation error")
                return None
            finally:
                await browser.close()

    def _post_via_browser(
        self, image_paths: List[Path], caption: str
    ) -> Optional[PublishResult]:
        return asyncio.run(self._post_via_browser_async(image_paths, caption))

    # ── Main publish ──────────────────────────────────────────────────────

    def publish(self, post_package: Any) -> PublishResult:
        caption_body = post_package.formatted_captions.get("tiktok", "")
        if len(caption_body) > 2200:
            caption_body = caption_body[:2197] + "..."

        # Strategy 1: API (if access token configured)
        public_urls = [u for u in (post_package.public_image_urls or []) if u]
        if public_urls and self._access_token:
            result = self._post_via_api(public_urls, caption_body)
            if result:
                self.update_db_status(post_package.post_id, result)
                return result

        # Strategy 2: Browser automation
        image_paths = [Path(p) for p in (post_package.all_images or []) if Path(p).exists()]
        if image_paths:
            result = self._post_via_browser(image_paths, caption_body)
            if result:
                self.update_db_status(post_package.post_id, result)
                return result

        # All strategies failed
        result = PublishResult(
            success=False,
            error=(
                "All TikTok upload strategies failed. "
                "Options: (1) Set TIKTOK_ACCESS_TOKEN in .env for API access, "
                "(2) Re-export fresh TikTok browser cookies to TIKTOK_COOKIES_PATH."
            ),
        )
        logger.error("TikTok publish failed for post {} — marking failed, continuing", post_package.post_id)
        self.update_db_status(post_package.post_id, result)
        return result
