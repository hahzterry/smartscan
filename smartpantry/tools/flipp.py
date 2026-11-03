"""Flipp deals connector: weekly-ad prices by postal code.

Backend is Flipp's own web API (``backflipp.wishabi.com``) — the same
unauthenticated JSON endpoints flipp.com loads. There is no official
documentation or contract, so treat it as fragile and demo-grade:

- fetch once per postal code per week and cache to disk; rehearsals and
  the on-stage demo replay from cache and never touch the network;
- fail soft: any error means misses, and the run continues on other
  sources with deals marked unavailable;
- respect validity: deals past valid-to are dropped, never used;
- label output: observations carry ad-price validity and condition flags
  (loyalty card, coupon, multi-buy) for the plan renderer;
- match strictly: flyer names are vague, so a deal only counts when the
  product match clears a strict threshold.

Response parsing is deliberately tolerant of several key spellings —
when the shape drifts, unknown fields are skipped, not crashed on.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..config import get_config
from ..schemas import PriceObservation, Product
from . import product_match, unit_price
from .store_connectors import (
    BaseConnector,
    BrowserConnector,
    ConnectorRegistry,
    PriceTable,
)

FLIPP_BASE = "https://backflipp.wishabi.com"
USER_AGENT = "smartpantry/0.1 (personal use; weekly ad cache)"
CACHE_TTL_DAYS = 7
DEFAULT_MERCHANTS = ["Mariano's", "Target", "Walmart"]

# Strict match floor for vague flyer names (plan: claim a saving only on
# a confident match). The configured threshold applies when higher.
STRICT_MATCH_FLOOR = 0.75

_CONDITION_PATTERNS = [
    (re.compile(r"\bloyalty\b|\bwith card\b|\bmembers?\b|\brewards?\b|\bcircle\b",
                re.IGNORECASE), "loyalty_card"),
    (re.compile(r"\bcoupon\b|\bclip\b|\bdigital\b|\brebate\b|\bcash back\b",
                re.IGNORECASE), "coupon"),
    (re.compile(r"\bbuy \d|\bbogo\b|\bmust buy\b|\bwhen you buy\b|\b2/\$|\b3/\$",
                re.IGNORECASE), "multi_buy"),
    (re.compile(r"\blimit \d|\bwhile supplies\b|\bin-store only\b",
                re.IGNORECASE), "limit"),
]


def detect_conditions(*texts: str) -> List[str]:
    """Flag deal conditions (loyalty, coupon, multi-buy, limits)."""
    joined = " ".join(t for t in texts if t)
    return sorted({flag for pattern, flag in _CONDITION_PATTERNS
                   if pattern.search(joined)})


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().lstrip("$").replace(",", ""))
        except ValueError:
            return None
    return None


def _first(node: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if node.get(key) is not None:
            return node[key]
    return None


def _as_date(value: Any) -> str:
    """Normalise validity dates to YYYY-MM-DD; '' when unknown."""
    if isinstance(value, (int, float)) and value > 0:
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).strftime(
                "%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(text[:len(fmt)], fmt).strftime(
                    "%Y-%m-%d")
            except ValueError:
                continue
        match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
        if match:
            return match.group(1)
    return ""


def parse_deal(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise one raw Flipp item into a deal dict (or None to skip)."""
    if not isinstance(raw, dict):
        return None
    name = _first(raw, "name", "title", "description", "item_name")
    if not name or not isinstance(name, str):
        return None
    price = _as_float(_first(raw, "sale_price", "current_price", "price",
                             "deal_price", "formatted_sale_price"))
    if price is None or price < 0:
        return None
    merchant = _first(raw, "merchant", "store", "retailer", "merchant_name",
                      "store_name") or ""
    details = _first(raw, "details", "fine_print", "terms", "description2",
                     "long_description") or ""
    valid_from = _as_date(_first(raw, "valid_from", "start_date", "valid_from_date",
                                 "flyer_start", "starts_at"))
    valid_to = _as_date(_first(raw, "valid_to", "end_date", "valid_to_date",
                               "flyer_end", "expires_at", "ends_at"))
    original = _as_float(_first(raw, "original_price", "regular_price",
                                "was_price", "pre_price"))
    return {"name": name.strip(),
            "price": price,
            "original_price": original,
            "merchant": str(merchant).strip(),
            "details": str(details),
            "valid_from": valid_from,
            "valid_to": valid_to,
            "conditions": detect_conditions(name, str(details))}


def parse_search_payload(payload: Any) -> List[Dict[str, Any]]:
    """Extract deal dicts from a search response of unknown exact shape."""
    if isinstance(payload, dict):
        for key in ("items", "results", "deals", "data"):
            node = payload.get(key)
            if isinstance(node, list):
                items = node
                break
            if isinstance(node, dict):
                for nested in ("items", "results"):
                    if isinstance(node.get(nested), list):
                        items = node[nested]
                        break
                else:
                    continue
                break
        else:
            return []
    elif isinstance(payload, list):
        items = payload
    else:
        return []
    deals = []
    for raw in items:
        deal = parse_deal(raw)
        if deal is not None:
            deals.append(deal)
    return deals


class FlippDealsConnector(BaseConnector):
    """Weekly-ad deals from Flipp, cached per postal code per week."""

    source_type = "flyer"
    tool_name = "flyer_connector"

    def __init__(self, postal_code: Optional[str] = None,
                 merchants: Optional[List[str]] = None,
                 cache_path: Optional[str] = None,
                 replay_only: bool = False,
                 base: Optional[str] = None,
                 min_interval_s: float = 1.0,
                 timeout: int = 15) -> None:
        super().__init__(min_interval_s=min_interval_s)
        self.postal_code = postal_code or os.environ.get(
            "FLIPP_POSTAL_CODE", "")
        env_merchants = os.environ.get("FLIPP_MERCHANTS", "")
        self.merchants = merchants if merchants is not None else [
            m.strip() for m in env_merchants.split(",") if m.strip()]
        self.cache_path = cache_path or os.environ.get(
            "FLIPP_CACHE_PATH", "flipp_cache.json")
        self.replay_only = replay_only or os.environ.get(
            "FLIPP_REPLAY_ONLY", "") == "1"
        self.base = (base or os.environ.get("FLIPP_API_BASE")
                     or FLIPP_BASE).rstrip("/")
        self.timeout = timeout
        self._memory_cache: Optional[List[Dict[str, Any]]] = None

    @property
    def configured(self) -> bool:
        return bool(self.postal_code)

    def _search_url(self, query: str) -> str:
        params = {"q": query, "postal_code": self.postal_code,
                  "locale": "en-us"}
        return "%s/flipp/items/search?%s" % (
            self.base, urllib.parse.urlencode(params))

    def _fetch_week(self) -> List[Dict[str, Any]]:
        """Fetch this week's deals (one call, whole postal code)."""
        request = urllib.request.Request(
            self._search_url(""), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request,
                                    timeout=self.timeout) as response:
            if response.status != 200:
                raise urllib.error.URLError("HTTP %s" % response.status)
            payload = json.loads(response.read().decode("utf-8"))
        return parse_search_payload(payload)

    def _cache_is_fresh(self, cached: Dict[str, Any]) -> bool:
        if cached.get("postal_code") != self.postal_code:
            return False
        try:
            fetched = datetime.fromisoformat(cached["fetched_at"])
        except (KeyError, ValueError):
            return False
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - fetched
        return age.total_seconds() < CACHE_TTL_DAYS * 86400

    def _load_deals(self) -> List[Dict[str, Any]]:
        if self._memory_cache is not None:
            return self._memory_cache
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as handle:
                    cached = json.load(handle)
                if self._cache_is_fresh(cached):
                    self._memory_cache = cached.get("deals", [])
                    return self._memory_cache
            except (ValueError, OSError):
                pass
        if self.replay_only:
            self._memory_cache = []
            return []
        self._polite_wait()
        deals = self._fetch_week()
        try:
            with open(self.cache_path, "w", encoding="utf-8") as handle:
                json.dump({"postal_code": self.postal_code,
                           "fetched_at": datetime.now(timezone.utc).isoformat(),
                           "deals": deals}, handle)
        except OSError:
            pass
        self._memory_cache = deals
        return deals

    @staticmethod
    def _merchant_matches(deal_merchant: str, store: str) -> bool:
        deal = deal_merchant.strip().lower()
        want = store.strip().lower()
        if not deal or not want:
            return False
        return deal == want or deal in want or want in deal

    def fetch(self, product: Product, store: str, prices: PriceTable,
              fetched_at: Optional[str] = None) -> Optional[PriceObservation]:
        _ = prices
        if not self.configured:
            return None
        try:
            deals = self._load_deals()
        except Exception:
            return None
        if self.merchants and not any(
                self._merchant_matches(m, store) for m in self.merchants):
            return None
        threshold = max(get_config().confidence_threshold, STRICT_MATCH_FLOOR)
        best: Optional[Dict[str, Any]] = None
        best_score = 0.0
        for deal in deals:
            if not self._merchant_matches(deal["merchant"], store):
                continue
            _pid, score = product_match.match_product(
                deal["name"], "", [product])
            if score > best_score:
                best_score = score
                best = deal
        if best is None or best_score < threshold:
            return None
        try:
            unit, base = unit_price.normalize(
                best["price"], product.size_value, product.size_unit)
            confidence = 0.75
        except ValueError:
            unit, base, confidence = round(best["price"], 4), "unparsed", 0.4
        stamp = fetched_at or datetime.now(timezone.utc).isoformat()
        return PriceObservation(
            observation_id="%s:%s:%s:%s" % (store, product.product_id,
                                            self.source_type, stamp),
            product_id=product.product_id, store=store,
            price=round(best["price"], 4), unit_price=unit, unit=base,
            source_type=self.source_type, fetched_at=stamp,
            confidence=confidence, valid_from=best["valid_from"],
            valid_to=best["valid_to"], conditions=list(best["conditions"]))


def flipp_registry(postal_code: Optional[str] = None,
                   merchants: Optional[List[str]] = None,
                   replay_only: bool = False,
                   min_interval_s: float = 1.0) -> ConnectorRegistry:
    """Registry with Flipp weekly ads as the price source.

    No Kroger or retailer-API dependency: every store resolves through
    Flipp deals for the postal code, with the browser fallback behind it.
    """
    registry = ConnectorRegistry(min_interval_s=0.0)
    flipp = FlippDealsConnector(postal_code=postal_code, merchants=merchants,
                                replay_only=replay_only,
                                min_interval_s=min_interval_s)
    chain = [flipp, registry.connectors["flyer"],
             BrowserConnector(min_interval_s=max(min_interval_s, 2.0))]
    # Flipp matches any merchant by name, so every store shares one chain.
    registry.ordered_for_store = lambda store: list(chain)
    return registry
