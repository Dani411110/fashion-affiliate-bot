"""Image download, resize, and hashing utilities."""

import io
from pathlib import Path

import requests
import imagehash
from PIL import Image, ImageOps

from utils.logger import get_logger
from utils.retry import retry_on_network_error

logger = get_logger(__name__)

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920


@retry_on_network_error
def download_image(url: str, dest_path: Path) -> Path:
    """Download an image from *url* and save it to *dest_path*.

    Returns the destination path on success.
    Raises requests.HTTPError on non-200 status.
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    logger.debug("Downloading image: {}", url)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=30, stream=True)
    resp.raise_for_status()

    with dest_path.open("wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            fh.write(chunk)

    size_kb = dest_path.stat().st_size / 1024
    logger.debug("Saved {} ({:.1f} KB)", dest_path.name, size_kb)
    return dest_path


def resize_image(path: Path, width: int, height: int) -> Path:
    """Resize *path* to exactly *width* x *height* using letterbox / pillarbox.

    Black bars are added as needed so the original aspect ratio is preserved.
    Modifies the file in-place and returns *path*.
    """
    path = Path(path)
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            img = ImageOps.fit(img, (width, height), method=Image.LANCZOS, centering=(0.5, 0.5))
            img.save(path, quality=92, optimize=True)
        logger.debug("Resized {} → {}×{}", path.name, width, height)
    except Exception:
        logger.exception("Failed to resize {}", path)
        raise
    return path


def make_vertical(path: Path) -> Path:
    """Resize / crop *path* to 1080×1920 (9:16 vertical format).

    Returns *path* after modifying in-place.
    """
    return resize_image(path, TARGET_WIDTH, TARGET_HEIGHT)


def get_image_hash(path: Path) -> str:
    """Return a perceptual hash string for the image at *path*."""
    path = Path(path)
    try:
        with Image.open(path) as img:
            h = imagehash.phash(img)
        return str(h)
    except Exception:
        logger.exception("Failed to hash {}", path)
        raise


def images_are_similar(hash_a: str, hash_b: str, threshold: int = 10) -> bool:
    """Return True when two perceptual hash strings differ by ≤ *threshold* bits."""
    try:
        ha = imagehash.hex_to_hash(hash_a)
        hb = imagehash.hex_to_hash(hash_b)
        return (ha - hb) <= threshold
    except Exception:
        logger.warning("Could not compare hashes {} vs {}", hash_a, hash_b)
        return False
