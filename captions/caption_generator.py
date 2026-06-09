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

_GENERATION_PROMPT = """Ești un creator de conținut fashion specializat în marketing afiliat.
Vei primi o imagine de inspirație vestimentară și o listă de produse disponibile la cumpărare.

Scrie conținut atractiv pentru postare. Răspunde DOAR cu JSON valid în această structură exactă:
{{
  "title": "Titlu captivant (max 10 cuvinte, fără hashtag-uri)",
  "caption": "Text caption captivant (2-4 propoziții). Menționează vibe-ul/estetica. NU include link-uri de produse aici.",
  "hashtags": ["hashtag1", "hashtag2", ...] // 15-25 hashtag-uri relevante, fără simbolul #
}}

Context:
- Categorie: {category}
- Produse în această postare: {product_summary}
- Platformă: {platform}

Reguli de stil (SCRIE TOTUL ÎN ROMÂNĂ):
- Sună ca un creator real de fashion, nu ca o pagină de vânzări.
- CTA subtil și util: "linkuri mai jos", "lista de produse mai jos" sau similar.
- Nu promite exagerat calitate, autenticitate, viteză de livrare sau identitate de brand.
- Menționează mai întâi estetica/vibe-ul, apoi conectează produsele natural.
- Evită fraze generice ca "TREBUIE să ai asta" dacă nu se potrivește platformei.

Reguli per platformă:
- Reddit: titlul să fie informativ, nu clickbait. Caption practic și orientat spre linkuri.
- TikTok: hook puternic, propoziții scurte, ton creator, 1 CTA subtil.
- Instagram: caption șlefuit, limbaj outfit/vibe, menționare clară a produselor.
- YouTube: titlu și descriere SEO-friendly pentru Shorts.

Hashtag-urile să reflecte estetica, articolele și publicul țintă. Hashtag-urile pot fi în engleză pentru reach mai mare."""

_FALLBACK_CAPTIONS = {
    "reddit": {
        "title": "Cele mai bune piese fashion la prețuri mici",
        "caption": "Priviți aceste piese incredibile! Calitate bună la prețuri accesibile. Linkuri mai jos.",
        "hashtags": ["fashion", "streetwear", "outfitinspo", "affordablefashion", "ootd"],
    },
    "tiktok": {
        "title": "Piese fashion la prețuri imbatabile ✨",
        "caption": "Găsești piese fashion care arată scumpe la prețuri mici! Linkuri în bio.",
        "hashtags": ["fashion", "outfitinspo", "affordablefashion", "fyp", "fashiontiktok"],
    },
    "instagram": {
        "title": "Inspirație outfit",
        "caption": "Îți ridici garderoba fără să golești bugetul. Verifică linkurile de produse!",
        "hashtags": ["fashion", "ootd", "outfitinspo", "style", "affordablefashion"],
    },
    "youtube": {
        "title": "Cele mai bune piese fashion ieftine | Haul",
        "caption": "Descoperă piese fashion la prețuri imbatabile. Toate linkurile mai jos!",
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
