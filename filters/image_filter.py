"""GPT-4o Vision image filter — rejects low-quality or inappropriate images."""

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import openai

from utils.logger import get_logger
from utils.retry import retry_on_api_error

logger = get_logger(__name__)

_SYSTEM_PROMPT = """You are a strict fashion content quality reviewer.
Analyse the image and decide whether it is suitable as an outfit inspiration image.

Reject the image (approved=false) if ANY of the following apply:
- No clearly visible person or full/partial outfit
- Image is blurry, heavily compressed, or very low resolution
- Large visible watermark covering key areas
- Chaotic multi-outfit collage with no clear focus
- Inappropriate, adult, or offensive content
- Bad crop: head cut off at unnatural point, or body severely cropped in a way that makes the outfit unclear
- Brand logos dominating the image (large Nike swoosh, Supreme box logo, etc.)
- Screenshot of a social media post or web page

Approve (approved=true) if the image shows a clear, in-focus outfit or clothing style that would
inspire a purchase, even if it is a flat lay, mannequin, or model photo.

Respond ONLY with valid JSON:
{"approved": true/false, "reason": "brief reason", "confidence": 0.0-1.0}"""


class FilterError(Exception):
    pass


@dataclass
class FilterResult:
    approved: bool
    reason: str
    confidence: float


class ImageFilter:
    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self._client = openai.OpenAI(api_key=api_key)
        self._model = model

    def _encode_image(self, image_path: Path) -> str:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    @retry_on_api_error
    def check_image(self, image_path: Path) -> FilterResult:
        """Run GPT-4o Vision check. Returns FilterResult.

        Returns approved=False on any API failure to fail safe.
        """
        image_path = Path(image_path)
        if not image_path.exists():
            logger.warning("Image not found for filter check: {}", image_path)
            return FilterResult(approved=False, reason="File not found", confidence=1.0)

        try:
            b64 = self._encode_image(image_path)
            ext = image_path.suffix.lower().lstrip(".")
            mime = f"image/{ext}" if ext in ("jpg", "jpeg", "png", "webp", "gif") else "image/jpeg"

            response = self._client.chat.completions.create(
                model=self._model,
                max_tokens=150,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime};base64,{b64}",
                                    "detail": "low",
                                },
                            },
                            {
                                "type": "text",
                                "text": "Evaluate this image and respond with the JSON.",
                            },
                        ],
                    },
                ],
            )

            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            data = json.loads(raw)
            result = FilterResult(
                approved=bool(data.get("approved", False)),
                reason=str(data.get("reason", "")),
                confidence=float(data.get("confidence", 0.5)),
            )
            status = "APPROVED" if result.approved else "REJECTED"
            logger.info(
                "Filter {}: {} — {} (conf={:.2f})",
                status,
                image_path.name,
                result.reason,
                result.confidence,
            )
            return result

        except openai.APIError as exc:
            logger.exception("OpenAI API error during image filter for {}", image_path)
            return FilterResult(approved=False, reason=f"API error: {exc}", confidence=1.0)
        except json.JSONDecodeError as exc:
            logger.exception("Invalid JSON from filter model for {}", image_path)
            return FilterResult(approved=False, reason=f"JSON parse error: {exc}", confidence=1.0)
        except Exception as exc:
            logger.exception("Unexpected filter error for {}", image_path)
            return FilterResult(approved=False, reason=f"Unexpected error: {exc}", confidence=1.0)


_filter_instance: Optional[ImageFilter] = None


def get_image_filter() -> ImageFilter:
    global _filter_instance
    if _filter_instance is None:
        from config.settings import get_settings
        s = get_settings()
        _filter_instance = ImageFilter(s.openai_api_key, s.openai_model)
    return _filter_instance
