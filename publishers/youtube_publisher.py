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


def _build_simple_video(image_paths: List[Path], output_path: Path, seconds_per_image: float = 3.0) -> Path:
    """Concatenate images into a silent MP4 using ffmpeg.

    No effects, no music — just images shown for *seconds_per_image* each.
    Returns output_path.
    """
    valid = [p for p in image_paths if p.exists()]
    if not valid:
        raise ValueError("No valid images for YouTube video")

    with tempfile.TemporaryDirectory() as tmpdir:
        concat_file = Path(tmpdir) / "concat.txt"
        lines = []
        for img in valid:
            lines.append(f"file '{img.resolve()}'")
            lines.append(f"duration {seconds_per_image}")
        # Repeat last image to avoid ffmpeg EOF truncation
        lines.append(f"file '{valid[-1].resolve()}'")
        concat_file.write_text("\n".join(lines), encoding="utf-8")

        total_dur = seconds_per_image * len(valid)

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-vf", (
                "scale=1080:1920:force_original_aspect_ratio=decrease,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,"
                "setsar=1"
            ),
            "-an",                          # no audio
            "-c:v", "libx264",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-t", str(total_dur),
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("ffmpeg error: {}", result.stderr[-2000:])
            raise RuntimeError(f"ffmpeg failed with code {result.returncode}")

    logger.info(
        "YouTube simple video created: {} ({:.0f}s, {} images)",
        output_path.name, total_dur, len(valid),
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
        """Write YOUTUBE_TOKEN_JSON env var to disk if token file is missing."""
        if self._token_json and not self._token_path.exists():
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_path.write_text(self._token_json, encoding="utf-8")
            logger.info("YouTube token bootstrapped from env var to {}", self._token_path)

    def _get_service(self):
        if self._service:
            return self._service

        creds: Optional[Credentials] = None

        if self._token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self._token_path), _YT_SCOPES)
            except Exception:
                creds = None

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid:
            # Write secrets file if raw JSON string was passed
            secrets_path = self._secrets_json
            if not Path(secrets_path).exists():
                tmp = Path("data/youtube_client_secrets.json")
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(self._secrets_json, encoding="utf-8")
                secrets_path = str(tmp)

            flow = InstalledAppFlow.from_client_secrets_file(secrets_path, _YT_SCOPES)
            oauth_port = int(os.getenv("YOUTUBE_OAUTH_PORT", "8081"))
            creds = flow.run_local_server(port=oauth_port)
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_path.write_text(creds.to_json(), encoding="utf-8")
            logger.info("YouTube OAuth token saved to {}", self._token_path)

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
        """Return image paths for the video, re-downloading from public_image_urls if local files are gone."""
        local_paths = [Path(p) for p in (post_package.all_images or [])]
        existing = [p for p in local_paths if p.exists()]

        if len(existing) == len(local_paths) and existing:
            return existing

        # Some or all local images are missing — re-download from public URLs
        public_urls = list(post_package.public_image_urls or [])
        if not public_urls:
            return existing

        missing_count = len(local_paths) - len(existing)
        logger.warning(
            "{}/{} local images missing — re-downloading from public_image_urls",
            missing_count, len(local_paths),
        )

        volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "data")
        dl_dir = Path(volume) / "temp" / "yt_fallback"
        dl_dir.mkdir(parents=True, exist_ok=True)

        fallback: List[Path] = []
        for idx, url in enumerate(public_urls):
            if not url:
                continue
            dest = dl_dir / f"yt_img_{post_package.post_id}_{idx:02d}.jpg"
            if dest.exists():
                fallback.append(dest)
                continue
            try:
                resp = requests.get(
                    url, timeout=30,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                    stream=True,
                )
                resp.raise_for_status()
                dest.write_bytes(resp.content)
                fallback.append(dest)
                logger.debug("Re-downloaded image {} → {}", idx, dest.name)
            except Exception as exc:
                logger.warning("Could not re-download image {}: {}", url[:80], exc)

        return fallback if fallback else existing

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

            captions = post_package.captions.get("youtube", {})
            title = captions.get("title", "Fashion Finds")
            description = post_package.formatted_captions.get("youtube", "")
            hashtags = captions.get("hashtags", [])
            tags = [h.lstrip("#") for h in hashtags][:500]

            video_id = self._upload_video(video_path, title, description, tags)
            url = f"https://www.youtube.com/shorts/{video_id}"

            # Pinned comment with all product links
            product_links = "\n".join(
                f"🛍 {p.get('name', 'Product')} — ${p.get('price', 0):.2f}: {p.get('mulebuy_link', '')}"
                for p in (post_package.products or [])
            )
            if product_links:
                try:
                    self._pin_comment(video_id, product_links)
                except Exception:
                    logger.warning("Could not pin product links comment on YouTube {}", video_id)

            # Clean up temp video
            try:
                video_path.unlink(missing_ok=True)
            except Exception:
                pass

            result = PublishResult(success=True, platform_post_id=video_id, url=url)

        except FileNotFoundError as exc:
            result = PublishResult(success=False, error=str(exc))
        except Exception as exc:
            logger.exception("YouTube publish failed")
            result = PublishResult(success=False, error=str(exc))

        self.update_db_status(post_package.post_id, result)
        return result
