"""Vertical video slideshow creator with Ken Burns effect and crossfades."""

import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from utils.logger import get_logger
from utils.image_utils import make_vertical

logger = get_logger(__name__)

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920


class SlideshowCreator:
    def __init__(self, duration_per_image: float = 3.0):
        self.duration_per_image = duration_per_image

    def create_slideshow(
        self,
        image_paths: List[Path],
        audio_path: Path,
        output_path: Path,
        duration_per_image: Optional[float] = None,
    ) -> Path:
        """Create a 1080×1920 MP4 slideshow with Ken Burns effect and crossfades.

        Falls back to a simpler ffmpeg concat approach if moviepy fails.
        """
        dur = duration_per_image or self.duration_per_image
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        prepared = self._prepare_images(image_paths)
        if not prepared:
            raise ValueError("No valid images to create slideshow")

        try:
            return self._moviepy_slideshow(prepared, audio_path, output_path, dur)
        except Exception as mp_err:
            logger.warning("moviepy failed ({}), falling back to ffmpeg", mp_err)
            try:
                return self._ffmpeg_slideshow(prepared, audio_path, output_path, dur)
            except Exception:
                logger.exception("ffmpeg fallback also failed")
                raise

    def _prepare_images(self, image_paths: List[Path]) -> List[Path]:
        prepared = []
        for p in image_paths:
            p = Path(p)
            if not p.exists():
                logger.warning("Image not found, skipping: {}", p)
                continue
            try:
                make_vertical(p)
                prepared.append(p)
            except Exception:
                logger.exception("Failed to prepare image: {}", p)
        return prepared

    def _moviepy_slideshow(
        self,
        image_paths: List[Path],
        audio_path: Path,
        output_path: Path,
        dur: float,
    ) -> Path:
        from moviepy.editor import (
            ImageClip,
            AudioFileClip,
            CompositeVideoClip,
            concatenate_videoclips,
        )
        from moviepy.video.fx.all import crop

        clips = []
        for img_path in image_paths:
            base = ImageClip(str(img_path)).set_duration(dur)

            # Ken Burns: slow zoom from 100% to 108%
            def make_zoom(clip, speed=0.008):
                def zoom_effect(get_frame, t):
                    import numpy as np
                    from PIL import Image
                    scale = 1.0 + speed * t
                    frame = get_frame(t)
                    h, w = frame.shape[:2]
                    new_w = int(w * scale)
                    new_h = int(h * scale)
                    img = Image.fromarray(frame)
                    img = img.resize((new_w, new_h), Image.LANCZOS)
                    x_off = (new_w - w) // 2
                    y_off = (new_h - h) // 2
                    cropped = np.array(img)[y_off:y_off + h, x_off:x_off + w]
                    return cropped
                return clip.fl(zoom_effect)

            zoomed = make_zoom(base)
            clips.append(zoomed)

        if not clips:
            raise ValueError("No clips created")

        # 0.5s crossfade between clips
        final_clips = [clips[0]]
        for clip in clips[1:]:
            final_clips.append(clip.crossfadein(0.5))

        video = concatenate_videoclips(final_clips, method="compose", padding=-0.5)

        audio = AudioFileClip(str(audio_path))
        total_dur = video.duration
        if audio.duration < total_dur:
            loops = int(total_dur / audio.duration) + 1
            from moviepy.editor import concatenate_audioclips
            audio = concatenate_audioclips([audio] * loops)
        audio = audio.subclip(0, total_dur)
        audio = audio.audio_fadein(0.5).audio_fadeout(1.5)

        final = video.set_audio(audio)
        final.write_videofile(
            str(output_path),
            fps=30,
            codec="libx264",
            audio_codec="aac",
            bitrate="4000k",
            ffmpeg_params=["-crf", "23"],
            logger=None,
        )
        logger.info(
            "Slideshow created (moviepy): {} ({:.1f}s)", output_path.name, total_dur
        )
        return output_path

    def _ffmpeg_slideshow(
        self,
        image_paths: List[Path],
        audio_path: Path,
        output_path: Path,
        dur: float,
    ) -> Path:
        """Pure ffmpeg fallback using concat demuxer."""
        with tempfile.TemporaryDirectory() as tmpdir:
            concat_file = Path(tmpdir) / "concat.txt"
            lines = []
            for img in image_paths:
                lines.append(f"file '{img.resolve()}'")
                lines.append(f"duration {dur}")
            # repeat last image to avoid ffmpeg truncation
            lines.append(f"file '{image_paths[-1].resolve()}'")
            concat_file.write_text("\n".join(lines), encoding="utf-8")

            total_dur = dur * len(image_paths)

            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", str(concat_file),
                "-i", str(audio_path),
                "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
                       f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,"
                       f"zoompan=z='min(zoom+0.0015,1.5)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}",
                "-af", f"afade=t=in:st=0:d=0.5,afade=t=out:st={total_dur - 1.5}:d=1.5",
                "-t", str(total_dur),
                "-c:v", "libx264", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                str(output_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error("ffmpeg stderr: {}", result.stderr[-2000:])
                raise RuntimeError(f"ffmpeg exited {result.returncode}")

        logger.info("Slideshow created (ffmpeg): {}", output_path.name)
        return output_path

    def add_text_overlay(
        self,
        video_path: Path,
        text: str,
        position: str,
        output_path: Path,
    ) -> Path:
        """Burn a text overlay onto an existing video using ffmpeg.

        *position* can be 'top', 'bottom', or 'center'.
        """
        video_path = Path(video_path)
        output_path = Path(output_path)

        positions = {
            "top": "(w-text_w)/2:50",
            "bottom": "(w-text_w)/2:h-100",
            "center": "(w-text_w)/2:(h-text_h)/2",
        }
        xy = positions.get(position, positions["bottom"])
        safe_text = text.replace("'", "'\\''").replace(":", "\\:")

        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vf",
            (
                f"drawtext=text='{safe_text}':fontcolor=white:fontsize=48:"
                f"x={xy}:borderw=3:bordercolor=black:font=Arial"
            ),
            "-c:v", "libx264", "-crf", "23",
            "-c:a", "copy",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("Text overlay ffmpeg error: {}", result.stderr[-1000:])
            raise RuntimeError(f"ffmpeg text overlay failed: {result.returncode}")

        logger.info("Text overlay added to {}", output_path.name)
        return output_path


_creator_instance: Optional[SlideshowCreator] = None


def get_slideshow_creator() -> SlideshowCreator:
    global _creator_instance
    if _creator_instance is None:
        from config.settings import get_settings
        _creator_instance = SlideshowCreator(
            duration_per_image=get_settings().video_seconds_per_image
        )
    return _creator_instance
