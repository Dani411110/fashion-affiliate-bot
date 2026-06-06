"""GPT-4o caption generation with per-platform formatting."""

import base64
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import openai

from utils.logger import get_logger
from utils.retry import retry_on_api_error

logger = get_logger(__name__)

PLATFORMS = ("reddit", "tiktok", "instagram", "youtube")

_GENERATION_PROMPT = """You are a fashion content creator specialising in affiliate marketing.
You will receive an outfit inspiration image and a list of shoppable products.

Write engaging content for the post. Respond ONLY with valid JSON in this exact structure:
{{
  "title": "Catchy post title (max 10 words, no hashtags)",
  "caption": "Engaging caption body (2-4 sentences). Mention the vibe/aesthetic. Do NOT include product links here.",
  "hashtags": ["hashtag1", "hashtag2", ...] // 15-25 relevant hashtags, no # symbol
}}

Context:
- Category: {category}
- Products in this post: {product_summary}
- Platform: {platform}

For Reddit: title should be informative (e.g. "Found this amazing Korean streetwear look on Mulebuy").
For TikTok/Instagram: title is the first hook line of the caption.
For YouTube: title should be SEO-friendly.

Hashtags should reflect the aesthetic, items, and target audience."""

_FALLBACK_CAPTIONS = {
    "reddit": {
        "title": "Amazing Fashion Finds from Mulebuy",
        "caption": "Check out these incredible pieces! Great quality and affordable prices.",
        "hashtags": ["fashion", "streetwear", "outfitinspo", "mulebuy", "affordablefashion"],
    },
    "tiktok": {
        "title": "You NEED these fashion finds ✨",
        "caption": "Affordable fashion finds that look expensive! Links in bio.",
        "hashtags": ["fashion", "outfitinspo", "affordablefashion", "fyp", "fashiontiktok"],
    },
    "instagram": {
        "title": "Outfit inspiration",
        "caption": "Elevate your wardrobe without breaking the bank. Shop the links!",
        "hashtags": ["fashion", "ootd", "outfitinspo", "style", "affordablefashion"],
    },
    "youtube": {
        "title": "Best Affordable Fashion Finds | Mulebuy Haul",
        "caption": "Discover amazing fashion pieces at unbeatable prices. All links below!",
        "hashtags": ["fashion", "haul", "mulebuy", "affordablefashion", "outfitideas"],
    },
}


class CaptionGenerator:
    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self._client = openai.OpenAI(api_key=api_key)
        self._model = model

    def _encode_image(self, image_path: Path) -> str:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _build_product_summary(self, products: List[Dict[str, Any]]) -> str:
        lines = []
        for p in products:
            lines.append(
                f"- {p.get('name', 'Unknown')} (${p.get('price', 0):.2f}, {p.get('category', '')})"
            )
        return "\n".join(lines)

    @retry_on_api_error
    def _call_openai(
        self,
        image_path: Path,
        products: List[Dict[str, Any]],
        category_name: str,
        platform: str,
    ) -> Dict[str, Any]:
        b64 = self._encode_image(image_path)
        ext = image_path.suffix.lower().lstrip(".")
        mime = f"image/{ext}" if ext in ("jpg", "jpeg", "png", "webp") else "image/jpeg"

        prompt = _GENERATION_PROMPT.format(
            category=category_name,
            product_summary=self._build_product_summary(products),
            platform=platform,
        )

        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=600,
            messages=[
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
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:].strip()
        return json.loads(raw)

    def generate_caption(
        self,
        pinterest_image_path: Path,
        products: List[Dict[str, Any]],
        category_name: str,
        platform: str,
    ) -> Dict[str, Any]:
        """Generate a caption dict {title, caption, hashtags} for one platform."""
        try:
            data = self._call_openai(
                pinterest_image_path, products, category_name, platform
            )
            if not all(k in data for k in ("title", "caption", "hashtags")):
                raise ValueError("Missing required keys in OpenAI response")
            logger.info(
                "Caption generated for {} / {}: '{}'",
                category_name,
                platform,
                data["title"][:50],
            )
            return data
        except (openai.APIError, json.JSONDecodeError, ValueError, Exception) as exc:
            logger.exception(
                "Caption generation failed for {} / {} — using fallback: {}",
                category_name,
                platform,
                exc,
            )
            return dict(_FALLBACK_CAPTIONS.get(platform, _FALLBACK_CAPTIONS["reddit"]))

    def generate_all_platforms(
        self,
        pinterest_image_path: Path,
        products: List[Dict[str, Any]],
        category_name: str,
    ) -> Dict[str, Dict[str, Any]]:
        """Generate captions for all 4 platforms. Returns dict keyed by platform name."""
        result: Dict[str, Dict[str, Any]] = {}
        for platform in PLATFORMS:
            result[platform] = self.generate_caption(
                pinterest_image_path, products, category_name, platform
            )
        return result

    def format_for_platform(
        self,
        caption_data: Dict[str, Any],
        products: List[Dict[str, Any]],
        platform: str,
    ) -> str:
        """Return the final formatted string ready for posting to *platform*."""
        title = caption_data.get("title", "")
        caption = caption_data.get("caption", "")
        hashtags = caption_data.get("hashtags", [])
        tag_str = " ".join(f"#{h.lstrip('#')}" for h in hashtags)

        if platform == "reddit":
            lines = [f"**{title}**\n", caption, "\n\n---\n\n**Shop the look:**\n"]
            for p in products:
                lines.append(
                    f"- [{p.get('name', 'Product')} — ${p.get('price', 0):.2f}]({p.get('mulebuy_link', '')})"
                )
            lines.append(f"\n\n{tag_str}")
            return "\n".join(lines)

        elif platform == "tiktok":
            body = f"{title}\n\n{caption}"
            if len(body) > 150:
                body = body[:147] + "..."
            return f"{body}\n\n{tag_str}"

        elif platform == "instagram":
            links_block = "\n".join(
                f"🛍 {p.get('name', 'Product')} — ${p.get('price', 0):.2f}"
                for p in products
            )
            return f"{caption}\n\n{links_block}\n\n{tag_str}"

        elif platform == "youtube":
            links_block = "\n".join(
                f"▶ {p.get('name', 'Product')} — ${p.get('price', 0):.2f}: {p.get('mulebuy_link', '')}"
                for p in products
            )
            yt_tags = "  ".join(f"#{h.lstrip('#')}" for h in hashtags)
            return (
                f"{title}\n\n"
                f"{caption}\n\n"
                f"🛍 Shop All Products:\n{links_block}\n\n"
                f"{yt_tags}"
            )

        return f"{title}\n\n{caption}\n\n{tag_str}"


_generator_instance: Optional[CaptionGenerator] = None


def get_caption_generator() -> CaptionGenerator:
    global _generator_instance
    if _generator_instance is None:
        from config.settings import get_settings
        s = get_settings()
        _generator_instance = CaptionGenerator(s.openai_api_key, s.openai_model)
    return _generator_instance
