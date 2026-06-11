"""Instagram carousel publisher using the Graph API (no third-party library).

Flow:
  1. Create individual image containers (is_carousel_item=true) for each image
  2. Create a CAROUSEL container referencing all children
  3. Publish via media_publish
"""

import time
from typing import Any, List

import requests

from publishers.base_publisher import BasePublisher, PublishResult
from utils.logger import get_logger
from utils.retry import retry_on_network_error

logger = get_logger(__name__)

_GRAPH_BASE = "https://graph.instagram.com/v19.0"
_MAX_CAROUSEL_ITEMS = 10   # Instagram carousel limit
_POLL_INTERVAL = 5
_POLL_TIMEOUT = 120


class InstagramPublisher(BasePublisher):
    platform_name = "instagram"

    def __init__(self, access_token: str, user_id: str):
        self._token = access_token
        self._user_id = user_id
        logger.info("InstagramPublisher initialised (carousel mode) for user_id={}", user_id)

    # ── Step 1: create one image container (carousel item) ────────────────

    @retry_on_network_error
    def _create_item_container(self, image_url: str) -> str:
        """Upload one image as a carousel item. Returns container_id."""
        url = f"{_GRAPH_BASE}/{self._user_id}/media"
        payload = {
            "image_url": image_url,
            "is_carousel_item": "true",
            "access_token": self._token,
        }
        resp = requests.post(url, data=payload, timeout=30)
        resp.raise_for_status()
        container_id = resp.json()["id"]
        logger.debug("IG item container created: {}", container_id)
        return container_id

    # ── Step 2: create carousel container ────────────────────────────────

    @retry_on_network_error
    def _create_carousel_container(self, children: List[str], caption: str) -> str:
        """Create a CAROUSEL container from a list of item container IDs."""
        url = f"{_GRAPH_BASE}/{self._user_id}/media"
        payload = {
            "media_type": "CAROUSEL",
            "children": ",".join(children),
            "caption": caption,
            "access_token": self._token,
        }
        resp = requests.post(url, data=payload, timeout=30)
        resp.raise_for_status()
        carousel_id = resp.json()["id"]
        logger.debug("IG carousel container created: {}", carousel_id)
        return carousel_id

    # ── Step 3: poll until FINISHED ──────────────────────────────────────

    def _poll_until_ready(self, container_id: str) -> bool:
        deadline = time.time() + _POLL_TIMEOUT
        while time.time() < deadline:
            try:
                resp = requests.get(
                    f"{_GRAPH_BASE}/{container_id}",
                    params={"fields": "status_code", "access_token": self._token},
                    timeout=15,
                )
                resp.raise_for_status()
                status = resp.json().get("status_code", "")
                logger.debug("IG container {} status: {}", container_id, status)
                if status == "FINISHED":
                    return True
                if status == "ERROR":
                    logger.error("IG container {} in ERROR state", container_id)
                    return False
            except requests.RequestException:
                logger.warning("Poll request failed, retrying…")
            time.sleep(_POLL_INTERVAL)
        logger.error("Timed out waiting for IG container {}", container_id)
        return False

    # ── Step 4: publish ───────────────────────────────────────────────────

    @retry_on_network_error
    def _publish_container(self, carousel_id: str) -> str:
        """Publish the carousel container. Returns media_id."""
        url = f"{_GRAPH_BASE}/{self._user_id}/media_publish"
        payload = {
            "creation_id": carousel_id,
            "access_token": self._token,
        }
        resp = requests.post(url, data=payload, timeout=30)
        resp.raise_for_status()
        media_id = resp.json()["id"]
        logger.info("IG carousel published media_id={}", media_id)
        return media_id

    # ── Main publish ──────────────────────────────────────────────────────

    def publish(self, post_package: Any) -> PublishResult:
        try:
            public_urls = [u for u in (post_package.public_image_urls or []) if u]
            if not public_urls:
                raise ValueError(
                    "No public image URLs available for Instagram carousel. "
                    "Set DRIVE_FOLDER_QUEUE_ID in .env so images are uploaded to Drive."
                )

            # Instagram carousel: 2–10 items
            public_urls = public_urls[:_MAX_CAROUSEL_ITEMS]
            if len(public_urls) < 2:
                # Pad with the first image repeated if only one image available
                public_urls = public_urls * 2

            caption_body = post_package.formatted_captions.get("instagram", "")
            if len(caption_body) > 2200:
                caption_body = caption_body[:2197] + "..."

            # Create individual item containers
            logger.info("Creating {} IG carousel item containers…", len(public_urls))
            children: List[str] = []
            for i, img_url in enumerate(public_urls):
                try:
                    cid = self._create_item_container(img_url)
                    children.append(cid)
                    time.sleep(0.5)   # brief pause between API calls
                except Exception:
                    logger.exception("Failed to create IG item container for image {}", i)

            if len(children) < 2:
                raise RuntimeError(
                    f"Only {len(children)} item container(s) created — need at least 2 for carousel"
                )

            # Create carousel container
            carousel_id = self._create_carousel_container(children, caption_body)

            # Poll until ready
            ready = self._poll_until_ready(carousel_id)
            if not ready:
                raise RuntimeError(f"Carousel container {carousel_id} never reached FINISHED")

            # Publish
            media_id = self._publish_container(carousel_id)
            url = f"https://www.instagram.com/p/{media_id}/"

            result = PublishResult(success=True, platform_post_id=media_id, url=url)

        except requests.HTTPError as exc:
            body = exc.response.text[:300] if exc.response else ""
            result = PublishResult(
                success=False,
                error=f"HTTP {exc.response.status_code if exc.response else '?'}: {body}",
            )
        except Exception as exc:
            logger.exception("Instagram carousel publish failed")
            result = PublishResult(success=False, error=str(exc))

        self.update_db_status(post_package.post_id, result)
        return result
