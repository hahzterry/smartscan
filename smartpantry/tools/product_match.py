"""Canonical product matching across stores.

The same item appears under different names and sizes, so matching
scores name tokens, size proximity, and brand agreement. Low scores go
to the review queue — the matcher never guesses.
"""

import re
from typing import Dict, List, Optional, Sequence, Tuple

from ..schemas import Product
from . import unit_price

_WORD_RE = re.compile(r"[a-z0-9]+")


def normalize_text(text: str) -> List[str]:
    return _WORD_RE.findall((text or "").lower())


def token_score(description: str, canonical_name: str) -> float:
    """Recall of the canonical name in the description.

    Receipt lines are usually the canonical name plus a brand prefix, so
    extra description words (the brand) must not dilute the score.
    """
    desc = set(normalize_text(description))
    canon = set(normalize_text(canonical_name))
    if not desc or not canon:
        return 0.0
    return len(desc & canon) / len(canon)


def size_score(size_text: str, product: Product) -> float:
    if not size_text:
        return 0.5
    try:
        value, unit = unit_price.parse_size(size_text)
        family = unit_price.unit_family(unit)
        product_family = unit_price.unit_family(product.size_unit)
    except ValueError:
        return 0.5
    if family != product_family:
        return 0.0
    try:
        _, base = unit_price.normalize(1.0, value, unit)
        product_base_units = product.size_value * _to_base(product.size_unit)
        item_base_units = value * _to_base(unit)
    except ValueError:
        return 0.5
    if product_base_units <= 0 or item_base_units <= 0:
        return 0.5
    ratio = min(product_base_units, item_base_units) / max(
        product_base_units, item_base_units
    )
    _ = base
    return ratio


def _to_base(unit: str) -> float:
    canonical = unit_price.normalize_unit(unit)
    if canonical in unit_price.WEIGHT_TO_OZ:
        return unit_price.WEIGHT_TO_OZ[canonical]
    if canonical in unit_price.VOLUME_TO_FLOZ:
        return unit_price.VOLUME_TO_FLOZ[canonical]
    return 1.0


def brand_score(description: str, product: Product,
                catalog_brands: Sequence[str]) -> float:
    """1.0 when the product's brand words all appear, 0.0 when a rival
    catalog brand's words all appear, 0.5 when no brand is mentioned."""
    words = set(normalize_text(description))
    brand_words = set(normalize_text(product.brand))
    if brand_words and brand_words <= words:
        return 1.0
    for other in catalog_brands:
        other_words = set(normalize_text(other))
        if not other_words or other_words == brand_words:
            continue
        if other_words <= words:
            return 0.0
    return 0.5


def match_product(
    description: str,
    size_text: str,
    products: Sequence[Product],
    brand_aliases: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[str], float]:
    """Return (product_id or None, confidence) for the best candidate."""
    if not products:
        return None, 0.0
    catalog_brands = [p.brand for p in products]
    if brand_aliases:
        catalog_brands += list(brand_aliases.values())
    best_id: Optional[str] = None
    best_score = 0.0
    for product in products:
        name = token_score(description, product.canonical_name)
        size = size_score(size_text, product)
        brand = brand_score(description, product, catalog_brands)
        score = 0.5 * name + 0.3 * size + 0.2 * brand
        if score > best_score:
            best_score = score
            best_id = product.product_id
    return best_id, round(best_score, 4)
