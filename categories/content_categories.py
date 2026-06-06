"""Category-based product selection logic."""

import random
from typing import Any, Dict, List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)

CATEGORY_NAMES = {
    1: "Complete Outfit with Accessories",
    2: "Random Finds",
    3: "Most Popular",
    4: "Cheapest Finds",
}

_ACCESSORY_CATEGORIES = {"accessories", "bags", "shoes"}
_CLOTHING_CATEGORIES = {"tops", "bottoms"}


class CategorySelector:
    def __init__(
        self,
        min_count: int = 4,
        max_count: int = 7,
    ):
        self.min_count = min_count
        self.max_count = max_count

    def _filter_exclude(
        self,
        products: List[Dict[str, Any]],
        exclude_ids: Optional[List[int]],
    ) -> List[Dict[str, Any]]:
        if not exclude_ids:
            return list(products)
        excl = set(exclude_ids)
        return [p for p in products if p["sheet_row_index"] not in excl]

    def get_complete_outfit_products(
        self,
        all_products: List[Dict[str, Any]],
        count: Optional[int] = None,
        exclude_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Balanced selection: clothing items + accessories."""
        count = count or random.randint(self.min_count, self.max_count)
        pool = self._filter_exclude(all_products, exclude_ids)

        clothing = [p for p in pool if p.get("category", "").lower() in _CLOTHING_CATEGORIES]
        accessories = [p for p in pool if p.get("category", "").lower() in _ACCESSORY_CATEGORIES]
        other = [p for p in pool if p not in clothing and p not in accessories]

        # aim for ~60% clothing, ~40% accessories
        n_clothing = max(1, round(count * 0.6))
        n_accessories = max(1, count - n_clothing)

        selected: List[Dict[str, Any]] = []
        selected += random.sample(clothing, min(n_clothing, len(clothing)))
        selected += random.sample(accessories, min(n_accessories, len(accessories)))

        # pad with other items if not enough
        if len(selected) < count:
            remaining = [p for p in other if p not in selected]
            selected += random.sample(remaining, min(count - len(selected), len(remaining)))

        result = selected[:count]
        logger.info(
            "Complete Outfit: selected {}/{} products", len(result), count
        )
        return result

    def get_random_finds(
        self,
        all_products: List[Dict[str, Any]],
        count: Optional[int] = None,
        exclude_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Truly random selection."""
        count = count or random.randint(self.min_count, self.max_count)
        pool = self._filter_exclude(all_products, exclude_ids)
        result = random.sample(pool, min(count, len(pool)))
        logger.info("Random Finds: selected {}/{} products", len(result), count)
        return result

    def get_most_popular(
        self,
        all_products: List[Dict[str, Any]],
        count: Optional[int] = None,
        exclude_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Top popularity_score with slight randomization among the top 15."""
        count = count or random.randint(self.min_count, self.max_count)
        pool = self._filter_exclude(all_products, exclude_ids)
        sorted_pool = sorted(
            pool, key=lambda p: int(p.get("popularity_score", 0)), reverse=True
        )
        top_n = sorted_pool[:15]
        result = random.sample(top_n, min(count, len(top_n)))
        logger.info("Most Popular: selected {}/{} products", len(result), count)
        return result

    def get_cheapest_finds(
        self,
        all_products: List[Dict[str, Any]],
        count: Optional[int] = None,
        exclude_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Lowest price with slight randomization among the cheapest 15."""
        count = count or random.randint(self.min_count, self.max_count)
        pool = self._filter_exclude(all_products, exclude_ids)
        sorted_pool = sorted(pool, key=lambda p: float(p.get("price", 0)))
        bottom_n = sorted_pool[:15]
        result = random.sample(bottom_n, min(count, len(bottom_n)))
        logger.info("Cheapest Finds: selected {}/{} products", len(result), count)
        return result

    def select_by_name(
        self,
        category_name: str,
        all_products: List[Dict[str, Any]],
        count: Optional[int] = None,
        exclude_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Dispatch to the right method by category display name."""
        name_lower = category_name.lower()
        if "complete" in name_lower or "outfit" in name_lower:
            return self.get_complete_outfit_products(all_products, count, exclude_ids)
        elif "random" in name_lower:
            return self.get_random_finds(all_products, count, exclude_ids)
        elif "popular" in name_lower:
            return self.get_most_popular(all_products, count, exclude_ids)
        elif "cheap" in name_lower:
            return self.get_cheapest_finds(all_products, count, exclude_ids)
        else:
            logger.warning("Unknown category '{}', falling back to random", category_name)
            return self.get_random_finds(all_products, count, exclude_ids)


_selector_instance: Optional[CategorySelector] = None


def get_category_selector() -> CategorySelector:
    global _selector_instance
    if _selector_instance is None:
        from config.settings import get_settings
        s = get_settings()
        _selector_instance = CategorySelector(
            min_count=s.min_products_per_post,
            max_count=s.max_products_per_post,
        )
    return _selector_instance
