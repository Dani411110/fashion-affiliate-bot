"""Music provider: local files → Pixabay API → silent fallback."""

import random
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests

from config.settings import get_settings
from database.sqlite_db import get_db
from utils.logger import get_logger
from utils.retry import retry_on_network_error

logger = get_logger(__name__)

MOODS = ("upbeat", "chill", "dramatic", "trendy")

_PIXABAY_MOOD_QUERIES = {
    "upbeat": "energetic fashion upbeat",
    "chill": "chill lofi calm",
    "dramatic": "cinematic dramatic intense",
    "trendy": "pop trendy modern",
}


class MusicProvider:
    def __init__(
        self,
        music_folder: Path,
        pixabay_api_key: str,
        db=None,
    ):
        self._folder = Path(music_folder)
        self._folder.mkdir(parents=True, exist_ok=True)
        self._pixabay_key = pixabay_api_key
        self._db = db or get_db()

    def get_music_track(self, mood: str = "upbeat") -> Path:
        """Return a path to an MP3 file.

        Search order: local file matching mood → Pixabay download → silent audio.
        Avoids recently used tracks.
        """
        mood = mood.lower() if mood.lower() in MOODS else "upbeat"
        recently_used = set(self._db.get_recently_used_tracks(last_n=10))

        # 1. Local files
        track = self._find_local(mood, recently_used)
        if track:
            logger.info("Using local music track: {}", track.name)
            self._db.record_music_usage(str(track))
            return track

        # 2. Pixabay download
        if self._pixabay_key:
            track = self._download_from_pixabay(mood)
            if track:
                logger.info("Downloaded Pixabay track: {}", track.name)
                self._db.record_music_usage(str(track))
                return track

        # 3. Silent fallback
        logger.warning("No music found for mood '{}' — generating silent audio", mood)
        track = self._generate_silence()
        self._db.record_music_usage(str(track))
        return track

    def _find_local(self, mood: str, skip: set) -> Optional[Path]:
        candidates = [
            p
            for p in self._folder.glob("*.mp3")
            if mood in p.name.lower() and str(p) not in skip
        ]
        if not candidates:
            # fall back to any local mp3 not recently used
            candidates = [
                p for p in self._folder.glob("*.mp3") if str(p) not in skip
            ]
        if candidates:
            return random.choice(candidates)
        return None

    @retry_on_network_error
    def _download_from_pixabay(self, mood: str) -> Optional[Path]:
        query = _PIXABAY_MOOD_QUERIES.get(mood, mood)
        url = (
            "https://pixabay.com/api/videos/music/"
            f"?key={self._pixabay_key}&q={requests.utils.quote(query)}&per_page=10"
        )
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            if not hits:
                logger.warning("No Pixabay results for mood '{}'", mood)
                return None

            recently_used = set(self._db.get_recently_used_tracks(last_n=10))
            random.shuffle(hits)
            for hit in hits:
                audio_url = hit.get("audio", {}).get("mp3", "")
                if not audio_url:
                    continue
                filename = f"pixabay_{mood}_{hit.get('id', int(time.time()))}.mp3"
                dest = self._folder / filename
                if str(dest) in recently_used:
                    continue
                if dest.exists():
                    return dest
                r = requests.get(audio_url, timeout=60, stream=True)
                r.raise_for_status()
                with dest.open("wb") as fh:
                    for chunk in r.iter_content(8192):
                        fh.write(chunk)
                logger.info("Downloaded {} ({:.0f} KB)", filename, dest.stat().st_size / 1024)
                return dest
        except requests.RequestException:
            logger.exception("Pixabay download failed for mood '{}'", mood)
        return None

    def _generate_silence(self, duration: int = 60) -> Path:
        dest = self._folder / f"silence_{duration}s.mp3"
        if dest.exists():
            return dest
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi",
                    "-i", f"anullsrc=r=44100:cl=stereo",
                    "-t", str(duration),
                    "-c:a", "libmp3lame",
                    "-b:a", "128k",
                    str(dest),
                ],
                check=True,
                capture_output=True,
            )
            logger.info("Generated silence track: {}", dest.name)
        except Exception:
            logger.exception("ffmpeg silence generation failed")
            dest.write_bytes(b"")
        return dest


_provider_instance: Optional[MusicProvider] = None


def get_music_provider() -> MusicProvider:
    global _provider_instance
    if _provider_instance is None:
        s = get_settings()
        _provider_instance = MusicProvider(
            music_folder=s.music_folder,
            pixabay_api_key=s.pixabay_api_key,
        )
    return _provider_instance
