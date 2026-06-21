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

_GRAPH_BASE = "https://graph.facebook.com/v21.0"
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
        resp = requests.post(
            url,
            params={"access_token": self._token},
            data={"image_url": image_url, "is_carousel_item": "true"},
            timeout=30,
        )
        if not resp.ok:
            logger.error("IG container {} body: {}", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        container_id = resp.json()["id"]
        logger.debug("IG item container created: {}", container_id)
        return container_id

    # ── Step 2: create carousel container ────────────────────────────────

    @retry_on_network_error
    def _create_carousel_container(self, children: List[str], caption: str) -> str:
        """Create a CAROUSEL container from a list of item container IDs."""
        url = f"{_GRAPH_BASE}/{self._user_id}/media"
        resp = requests.post(
            url,
            params={"access_token": self._token},
            data={"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption},
            timeout=30,
        )
        if not resp.ok:
            logger.error("IG carousel {} body: {}", resp.status_code, resp.text[:500])
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
                )  # token passed as query param (standard for Graph API)
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
        resp = requests.post(
            url,
            params={"access_token": self._token},
            data={"creation_id": carousel_id},
            timeout=30,
        )
        if not resp.ok:
            logger.error("IG publish {} body: {}", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        media_id = resp.json()["id"]
        logger.info("IG carousel published media_id={}", media_id)
        return media_id

    _CAPTIONS = [
        (
            "🔥 Cele mai clean outfit-uri și sneakers 👀\n\n"
            "DM me pentru comandă 📩\n\n"
            "#Mulebuy #fashion #streetwear #sneakers #fyp"
        ),
        (
            "🔥 Clean outfits & insane sneaker deals 👀\n\n"
            "DM me to order 📩\n\n"
            "#Mulebuy #fashion #streetwear #sneakers #fyp"
        ),
    ]

    _POST_DELAY = 30  # seconds between the two IG posts (avoid rate-limit)

    # ── Main publish ──────────────────────────────────────────────────────

    def _publish_one(self, public_urls: List[str], caption: str, index: int) -> str:
        """Create containers, wait for FINISHED, publish. Returns media_id."""
        logger.info("IG post {}/2 — creating {} item containers…", index, len(public_urls))
        children: List[str] = []
        for i, img_url in enumerate(public_urls):
            try:
                cid = self._create_item_container(img_url)
                children.append(cid)
                time.sleep(0.5)
            except Exception:
                logger.exception("Failed to create IG item container for image {}", i)

        if len(children) < 2:
            raise RuntimeError(
                f"Only {len(children)} item container(s) created — need at least 2 for carousel"
            )

        carousel_id = self._create_carousel_container(children, caption)
        ready = self._poll_until_ready(carousel_id)
        if not ready:
            raise RuntimeError(f"Carousel container {carousel_id} never reached FINISHED")

        media_id = self._publish_container(carousel_id)
        logger.info("IG post {}/2 published: media_id={}", index, media_id)
        return media_id

    def publish(self, post_package: Any) -> PublishResult:
        if not self._token or not self._user_id:
            result = PublishResult(success=False, error="Instagram: INSTAGRAM_ACCESS_TOKEN sau INSTAGRAM_USER_ID nu sunt setate.")
            self.update_db_status(post_package.post_id, result)
            return result

        # Filtrare URL-uri: Instagram API accepta doar http/https, nu file://
        public_urls = [u for u in (post_package.public_image_urls or []) if u and u.startswith("http")]
        if not public_urls:
            result = PublishResult(success=False, error="No public HTTP image URLs for Instagram (RAILWAY_PUBLIC_DOMAIN not set?).")
            self.update_db_status(post_package.post_id, result)
            return result

        public_urls = public_urls[:_MAX_CAROUSEL_ITEMS]
        if len(public_urls) < 2:
            public_urls = public_urls * 2

        media_ids: List[str] = []
        errors: List[str] = []

        for idx, caption in enumerate(self._CAPTIONS, start=1):
            if idx > 1:
                logger.info("Waiting {}s before IG post {}/2…", self._POST_DELAY, idx)
                time.sleep(self._POST_DELAY)
            try:
                media_id = self._publish_one(public_urls, caption, idx)
                media_ids.append(media_id)
            except requests.HTTPError as exc:
                body = exc.response.text[:200] if exc.response else ""
                err = f"Post {idx} HTTP {exc.response.status_code if exc.response else '?'}: {body}"
                logger.error("IG post {}/2 failed: {}", idx, err)
                errors.append(err)
            except Exception as exc:
                logger.exception("IG post {}/2 failed", idx)
                errors.append(f"Post {idx}: {exc}")

        if media_ids:
            url = f"https://www.instagram.com/p/{media_ids[0]}/"
            error_note = f" (post 2 failed: {errors[-1]})" if errors else ""
            result = PublishResult(
                success=True,
                platform_post_id=",".join(media_ids),
                url=url,
                error=error_note,
            )
        else:
            result = PublishResult(success=False, error="; ".join(errors))

        self.update_db_status(post_package.post_id, result)
        return result
