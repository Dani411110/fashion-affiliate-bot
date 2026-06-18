"""YouTube publisher.

YouTube has no native carousel/photo post support.
We create a minimal silent slideshow video with ffmpeg (images concatenated,
no effects, no music) and upload it as a YouTube Short.

This is the simplest possible "carousel equivalent" for YouTube.
Each image is shown for VIDEO_SECONDS_PER_IMAGE seconds.
"""

import subprocess
import tempfile
import os
import requests
from pathlib import Path
from typing import Any, List, Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from publishers.base_publisher import BasePublisher, PublishResult
from utils.logger import get_logger
from utils.retry import retry_on_network_error

logger = get_logger(__name__)

_YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
_TOKEN_PATH = Path("data/youtube_token.json")
_MAX_TITLE_LEN = 100


_MIN_SHORT_DURATION = 15.0   # YouTube Shorts need ≥15s to perform well
_VIDEO_W, _VIDEO_H = 1080, 1920


def _prescale_images(image_paths: List[Path], tmp_dir: Path) -> List[Path]:
    """Pre-scale images to 1080x1920 with PIL before handing them to ffmpeg.

    ffmpeg loading full-resolution originals (e.g. 3000x4000) into memory for
    every frame causes OOM kills on Railway. Pre-scaling with PIL keeps each
    image under ~6MB in memory instead of ~36MB.
    """
    from PIL import Image as _PILImage
    scaled: List[Path] = []
    for i, src in enumerate(image_paths):
        dest = tmp_dir / f"pre_{i:02d}.jpg"
        try:
            with _PILImage.open(src) as im:
                im = im.convert("RGB")
                im.thumbnail((_VIDEO_W, _VIDEO_H), _PILImage.LANCZOS)
                # Paste onto black 1080x1920 canvas (letterbox / pillarbox)
                canvas = _PILImage.new("RGB", (_VIDEO_W, _VIDEO_H), (255, 255, 255))
                x = (_VIDEO_W - im.width) // 2
                y = (_VIDEO_H - im.height) // 2
                canvas.paste(im, (x, y))
                canvas.save(dest, "JPEG", quality=88, optimize=True)
            scaled.append(dest)
        except Exception as exc:
            logger.warning("Pre-scale failed for {}: {} — using original", src.name, exc)
            scaled.append(src)
    return scaled


def _build_simple_video(image_paths: List[Path], output_path: Path, seconds_per_image: float = 3.0) -> Path:
    """Concatenate pre-scaled images into an MP4 with silent AAC audio track.

    Images are pre-scaled to 1080x1920 with PIL before ffmpeg to avoid OOM
    kills on memory-limited containers (Railway free tier).
    """
    valid = [p for p in image_paths if p.exists()]
    if not valid:
        raise ValueError("No valid images for YouTube video")

    # Ensure each image shows long enough that total meets minimum
    spi = max(seconds_per_image, _MIN_SHORT_DURATION / len(valid))
    total_dur = spi * len(valid)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Pre-scale images to reduce ffmpeg memory usage
        scaled = _prescale_images(valid, tmp_path)

        concat_file = tmp_path / "concat.txt"
        lines = []
        for img in scaled:
            lines.append(f"file '{img.resolve()}'")
            lines.append(f"duration {spi}")
        # Repeat last image to avoid ffmpeg EOF truncation
        lines.append(f"file '{scaled[-1].resolve()}'")
        concat_file.write_text("\n".join(lines), encoding="utf-8")

        cmd = [
            "ffmpeg", "-y",
            # Video: pre-scaled image slideshow
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            # Audio: silent source (YouTube requires an audio stream)
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            # Images already at 1080x1920 — just ensure correct SAR
            "-vf", "setsar=1",
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "faster",    # less CPU/time; tiny quality trade-off
            "-crf", "23",
            "-r", "24",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-threads", "2",        # cap threads to limit peak memory
            "-t", str(total_dur),
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("ffmpeg error: {}", result.stderr[-2000:])
            raise RuntimeError(f"ffmpeg failed with code {result.returncode}")

    logger.info(
        "YouTube video created: {} ({:.0f}s, {} images, {:.1f}s/image)",
        output_path.name, total_dur, len(valid), spi,
    )
    return output_path


class YouTubePublisher(BasePublisher):
    platform_name = "youtube"

    def __init__(self, client_secrets_json: str, token_path: Optional[Path] = None,
                 token_json: Optional[str] = None, seconds_per_image: float = 3.0):
        self._secrets_json = client_secrets_json
        self._token_path = Path(token_path or _TOKEN_PATH)
        self._token_json = token_json  # raw JSON from env var (Railway)
        self._seconds_per_image = seconds_per_image
        self._service = None
        self._bootstrap_token()
        logger.info("YouTubePublisher initialised (simple image slideshow for YouTube)")

    def _bootstrap_token(self):
        """Write YOUTUBE_TOKEN_JSON env var to disk, always overwriting stale files."""
        if self._token_json:
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_path.write_text(self._token_json, encoding="utf-8")
            logger.info("YouTube token written from env var to {}", self._token_path)

    def _load_creds_from_file(self) -> Optional[Credentials]:
        """Load credentials from token file, with manual fallback for format compatibility."""
        if not self._token_path.exists():
            logger.warning("YouTube token file not found: {}", self._token_path)
            return None

        # Try the standard loader first
        try:
            creds = Credentials.from_authorized_user_file(str(self._token_path), _YT_SCOPES)
            logger.debug("YouTube creds loaded via from_authorized_user_file")
            return creds
        except Exception as exc:
            logger.warning("from_authorized_user_file failed ({}), trying manual load", exc)

        # Manual fallback: build Credentials directly from JSON fields
        try:
            import json as _json
            from datetime import datetime as _dt, timezone as _tz
            info = _json.loads(self._token_path.read_text(encoding="utf-8"))
            expiry = None
            if info.get("expiry"):
                try:
                    expiry = _dt.fromisoformat(info["expiry"].replace("Z", "+00:00")).replace(tzinfo=_tz.utc)
                except Exception:
                    pass
            creds = Credentials(
                token=info.get("token"),
                refresh_token=info.get("refresh_token"),
                token_uri=info.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=info.get("client_id"),
                client_secret=info.get("client_secret"),
                scopes=info.get("scopes") or _YT_SCOPES,
                expiry=expiry,
            )
            logger.info("YouTube creds loaded via manual JSON parse (expiry={})", info.get("expiry"))
            return creds
        except Exception as exc2:
            logger.error("Manual YouTube creds load also failed: {}", exc2)
            return None

    def _get_service(self):
        if self._service:
            return self._service

        creds = self._load_creds_from_file()

        if creds and creds.expired and creds.refresh_token:
            logger.info("YouTube token expired — refreshing via refresh_token")
            try:
                creds.refresh(Request())
                # Persist refreshed token
                try:
                    self._token_path.write_text(creds.to_json(), encoding="utf-8")
                    logger.info("Refreshed YouTube token saved to {}", self._token_path)
                except Exception as save_exc:
                    logger.warning("Could not save refreshed token: {}", save_exc)
            except Exception as exc:
                logger.error("YouTube token refresh failed: {}", exc)
                creds = None

        if not creds or not creds.valid:
            logger.error(
                "YouTube creds invalid after load attempt: creds={} valid={} expired={}",
                bool(creds), getattr(creds, "valid", None), getattr(creds, "expired", None),
            )
            raise RuntimeError(
                "YouTube OAuth token lipsa sau expirat. "
                "Ruleaza 'python main.py youtube-auth-url' local, completeaza OAuth, "
                "apoi seteaza YOUTUBE_TOKEN_JSON in Railway Variables."
            )

        self._service = build("youtube", "v3", credentials=creds, cache_discovery=False)
        return self._service

    @retry_on_network_error
    def _upload_video(self, video_path: Path, title: str, description: str, tags: list) -> str:
        service = self._get_service()
        body = {
            "snippet": {
                "title": title[:_MAX_TITLE_LEN],
                "description": description,
                "tags": tags,
                "categoryId": "26",     # Howto & Style
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }
        media = MediaFileUpload(
            str(video_path), mimetype="video/mp4", resumable=True, chunksize=10 * 1024 * 1024
        )
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.debug("YouTube upload {:.0f}%", status.progress() * 100)
        video_id = response["id"]
        logger.info("YouTube video uploaded: id={}", video_id)
        return video_id

    @retry_on_network_error
    def _pin_comment(self, video_id: str, comment_text: str):
        service = self._get_service()
        body = {
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": comment_text}},
            }
        }
        service.commentThreads().insert(part="snippet", body=body).execute()

    def _resolve_images(self, post_package: Any) -> List[Path]:
        """Return image paths for the video, re-downloading from source URLs if local files are gone.

        Google Drive /uc?export=download URLs return an HTML confirmation page for files
        over ~100KB (virus-scan warning), so we use the original product image_url instead.
        Layout of all_images: index 0 = Pinterest inspiration, index 1+ = products.
        """
        local_paths = [Path(p) for p in (post_package.all_images or [])]
        existing = [p for p in local_paths if p.exists()]

        if len(existing) == len(local_paths) and existing:
            return existing

        products = list(post_package.products or [])
        public_urls = list(post_package.public_image_urls or [])
        missing_count = len(local_paths) - len(existing)
        logger.warning(
            "{}/{} local images missing — re-downloading from source URLs",
            missing_count, len(local_paths),
        )

        volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "data")
        dl_dir = Path(volume) / "temp" / "yt_fallback"
        dl_dir.mkdir(parents=True, exist_ok=True)

        result: List[Path] = []
        for i, local_path in enumerate(local_paths):
            if local_path.exists():
                result.append(local_path)
                continue

            # Pick the best source URL for this slot:
            # slot 0 = Pinterest (use Drive URL as-is — it's a smaller file and usually works)
            # slot 1+ = product images (use original image_url from scraper, avoids Drive auth)
            if i == 0:
                url = public_urls[0] if public_urls else ""
            else:
                product_idx = i - 1
                if product_idx < len(products):
                    url = products[product_idx].get("image_url", "")
                else:
                    url = public_urls[i] if i < len(public_urls) else ""

            if not url:
                logger.warning("No fallback URL for image slot {}", i)
                continue

            dest = dl_dir / f"yt_img_{post_package.post_id}_{i:02d}.jpg"
            if dest.exists():
                result.append(dest)
                continue

            try:
                resp = requests.get(
                    url, timeout=30,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                )
                resp.raise_for_status()
                content = resp.content
                # Reject Drive HTML virus-scan pages (200 OK but not an image)
                if content[:5] in (b"<!DOC", b"<html", b"<HTML") or b"<html" in content[:200]:
                    logger.warning("Got HTML instead of image at slot {} — skipping: {}", i, url[:60])
                    continue
                dest.write_bytes(content)
                result.append(dest)
                logger.debug("Re-downloaded slot {} → {}", i, dest.name)
            except Exception as exc:
                logger.warning("Could not download image slot {}: {}", i, exc)

        return result if result else existing

    def publish(self, post_package: Any) -> PublishResult:
        try:
            image_paths = self._resolve_images(post_package)
            if not image_paths:
                raise FileNotFoundError("No images available for YouTube video")

            logger.info("Building YouTube video from {} images", len(image_paths))

            # Store temp video on persistent volume when possible
            volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "data")
            tmp_dir = Path(volume) / "temp"
            import tempfile as _tempfile
            tmp_video = Path(_tempfile.mktemp(suffix="_yt.mp4", dir=tmp_dir))
            tmp_video.parent.mkdir(parents=True, exist_ok=True)

            video_path = _build_simple_video(
                image_paths, tmp_video, self._seconds_per_image
            )

            hashtags = ["Mulebuy", "fashion", "streetwear", "sneakers", "fyp"]
            tags = hashtags[:500]
            hashtag_str = " ".join(f"#{h}" for h in hashtags)

            posts = [
                {
                    "title": "🔥 Cele mai clean outfit-uri și sneakers 👀",
                    "description": f"DM me pentru comandă 📩\n\n{hashtag_str}",
                },
                {
                    "title": "🔥 Clean outfits & insane sneaker deals 👀",
                    "description": f"DM me to order 📩\n\n{hashtag_str}",
                },
            ]

            # Pinned comment with all product links
            product_links = "\n".join(
                f"🛍 {p.get('name', 'Product')} — ${p.get('price', 0):.2f}: {p.get('mulebuy_link', '')}"
                for p in (post_package.products or [])
            )

            video_ids = []
            for p in posts:
                vid = self._upload_video(video_path, p["title"], p["description"], tags)
                video_ids.append(vid)
                if product_links:
                    try:
                        self._pin_comment(vid, product_links)
                    except Exception:
                        logger.warning("Could not pin comment on YouTube {}", vid)
                import time as _time
                _time.sleep(3)

            # Clean up temp video
            try:
                video_path.unlink(missing_ok=True)
            except Exception:
                pass

            url = f"https://www.youtube.com/shorts/{video_ids[0]}"
            result = PublishResult(success=True, platform_post_id=",".join(video_ids), url=url)

        except FileNotFoundError as exc:
            result = PublishResult(success=False, error=str(exc))
        except Exception as exc:
            logger.exception("YouTube publish failed")
            result = PublishResult(success=False, error=str(exc))

        self.update_db_status(post_package.post_id, result)
        return result
