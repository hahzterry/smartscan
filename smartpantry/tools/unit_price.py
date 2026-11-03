"""Unit-price normaliser: every price becomes $/base-unit.

Weight normalises to $/oz, volume to $/fl oz, count to $/ct. Unknown
units raise ``ValueError`` so callers route the item to the review queue
instead of guessing.
"""

import re
from typing import Dict, Tuple

WEIGHT_TO_OZ: Dict[str, float] = {
    "oz": 1.0,
    "lb": 16.0,
    "g": 0.035274,
    "kg": 35.274,
}

VOLUME_TO_FLOZ: Dict[str, float] = {
    "floz": 1.0,
    "ml": 0.033814,
    "l": 33.814,
}

COUNT_UNITS = ("ct", "ea")

_ALIASES = {
    "ounce": "oz", "ounces": "oz", "ozs": "oz",
    "pound": "lb", "pounds": "lb", "lbs": "lb",
    "gram": "g", "grams": "g",
    "kilogram": "kg", "kilograms": "kg", "kilo": "kg", "kilos": "kg",
    "milliliter": "ml", "milliliters": "ml", "millilitre": "ml",
    "millilitres": "ml",
    "liter": "l", "liters": "l", "litre": "l", "litres": "l",
    "fl oz": "floz", "flozs": "floz", "fluid ounce": "floz",
    "fluid ounces": "floz",
    "count": "ct", "counts": "ct",
    "each": "ea",
}

_SIZE_RE = re.compile(r"^\s*([\d]+(?:\.\d+)?)\s*([a-zA-Z][a-zA-Z ]*)\s*$")


def normalize_unit(unit: str) -> str:
    key = unit.strip().lower()
    key = _ALIASES.get(key, key)
    if key in WEIGHT_TO_OZ or key in VOLUME_TO_FLOZ or key in COUNT_UNITS:
        return key
    raise ValueError("unknown size unit: %r" % unit)


def unit_family(unit: str) -> str:
    canonical = normalize_unit(unit)
    if canonical in WEIGHT_TO_OZ:
        return "weight"
    if canonical in VOLUME_TO_FLOZ:
        return "volume"
    return "count"


def normalize(price: float, size_value: float, size_unit: str) -> Tuple[float, str]:
    """Return (unit_price, base_unit) for a package price."""
    if size_value <= 0:
        raise ValueError("size_value must be > 0")
    if price < 0:
        raise ValueError("price must be >= 0")
    canonical = normalize_unit(size_unit)
    if canonical in WEIGHT_TO_OZ:
        base_units = size_value * WEIGHT_TO_OZ[canonical]
        return round(price / base_units, 4), "oz"
    if canonical in VOLUME_TO_FLOZ:
        base_units = size_value * VOLUME_TO_FLOZ[canonical]
        return round(price / base_units, 4), "floz"
    return round(price / size_value, 4), "ct"


def parse_size(text: str) -> Tuple[float, str]:
    """Parse '12 oz' / '1 lb' / '500 g' into (value, canonical unit)."""
    match = _SIZE_RE.match(text or "")
    if not match:
        raise ValueError("could not parse size: %r" % text)
    return float(match.group(1)), normalize_unit(match.group(2))
