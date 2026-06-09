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

_GENERATION_PROMPT = """You are a fashion content creator. Write content for a social media post.

Respond ONLY with valid JSON:
{{
  "caption": "1 short sentence in English. Max 10 words.",
  "hashtags": ["hashtag1", "hashtag2", ...] // 10-15 hashtags, no # symbol
}}

Context:
- Category: {category}
- Products: {product_summary}
- Platform: {platform}

CAPTION — 1 sentence only, English, max 10 words. Example: "links below 👇" or "shop the fit 🛍"

HASHTAGS — English, relevant to category and items."""

_FALLBACK_CAPTIONS = {
    "reddit": {
        "title": "",  # overridden by category name
        "caption": "links below 👇",
        "hashtags": ["fashion", "streetwear", "outfitinspo", "affordablefashion", "ootd"],
    },
    "tiktok": {
        "title": "",
        "caption": "shop the fit 🛍",
        "hashtags": ["fashion", "outfitinspo", "affordablefashion", "fyp", "fashiontiktok"],
    },
    "instagram": {
        "title": "",
        "caption": "links below 👇",
        "hashtags": ["fashion", "ootd", "outfitinspo", "style", "affordablefashion"],
    },
    "youtube": {
        "title": "",
        "caption": "shop all links below 🛍",
        "hashtags": ["fashion", "haul", "affordablefashion", "outfitideas", "ootd"],
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
            price = float(p.get('price', 0) or 0)
            price_str = f"${price:.2f}, " if price > 0 else ""
            lines.append(f"- {p.get('name', 'Unknown')} ({price_str}{p.get('category', '')})")
        return "\n".join(lines)

    @staticmethod
    def _title_from_category(category_name: str) -> str:
        """Convert category name to a short title matching the Telegram button label."""
        mapping = {
            "complete outfit with accessories": "complete outfit",
            "random finds": "random finds",
            "most popular": "most popular",
            "cheapest finds": "cheap finds",
        }
        return mapping.get(category_name.lower(), category_name.lower())

    @retry_on_api_error
    def _call_openai(
        self,
        image_path: Path,
        products: List[Dict[str, Any]],
        category_name: str,
        platform: str,
    ) -> Dict[str, Any]:
        prompt = _GENERATION_PROMPT.format(
            category=category_name,
            product_summary=self._build_product_summary(products),
            platform=platform,
        )

        # Text-only call — caption and hashtags only, title comes from category
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
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
        title = self._title_from_category(category_name)
        try:
            data = self._call_openai(
                pinterest_image_path, products, category_name, platform
            )
            if not all(k in data for k in ("caption", "hashtags")):
                raise ValueError("Missing required keys in OpenAI response")
            data["title"] = title
            logger.info("Caption generated for {} / {}: '{}'", category_name, platform, title)
            return data
        except (openai.APIError, json.JSONDecodeError, ValueError, Exception) as exc:
            logger.exception(
                "Caption generation failed for {} / {} — using fallback: {}",
                category_name, platform, exc,
            )
            fallback = dict(_FALLBACK_CAPTIONS.get(platform, _FALLBACK_CAPTIONS["reddit"]))
            fallback["title"] = title
            return fallback

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
                price = float(p.get('price', 0) or 0)
                price_str = f" — ${price:.2f}" if price > 0 else ""
                lines.append(
                    f"- [{p.get('name', 'Product')}{price_str}]({p.get('mulebuy_link', '')})"
                )
            lines.append(f"\n\n{tag_str}")
            return "\n".join(lines)

        elif platform == "tiktok":
            body = f"{title}\n\n{caption}"
            if len(body) > 150:
                body = body[:147] + "..."
            return f"{body}\n\n{tag_str}"

        elif platform == "instagram":
            lines_ig = []
            for p in products:
                price = float(p.get('price', 0) or 0)
                price_str = f" — ${price:.2f}" if price > 0 else ""
                lines_ig.append(f"🛍 {p.get('name', 'Product')}{price_str}")
            return f"{caption}\n\n" + "\n".join(lines_ig) + f"\n\n{tag_str}"

        elif platform == "youtube":
            lines_yt = []
            for p in products:
                price = float(p.get('price', 0) or 0)
                price_str = f" — ${price:.2f}" if price > 0 else ""
                link = p.get('mulebuy_link', '')
                suffix = f": {link}" if link else ""
                lines_yt.append(f"▶ {p.get('name', 'Product')}{price_str}{suffix}")
            links_block = "\n".join(lines_yt)
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
