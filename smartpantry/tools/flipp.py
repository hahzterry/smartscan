"""CAMPDEALZ Flipp connector.

Retrieve advertised retail deals independently of purchase history.
Flipp uses undocumented endpoints; availability is not guaranteed.
"""

import hashlib
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

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
USER_AGENT = "SmartPantry/0.2"
CACHE_TTL_DAYS = 7
STRICT_MATCH_FLOOR = 0.75

DEFAULT_POSTAL_CODES = [
    "30344",
    "30349",
    "30331",
]

DEFAULT_MERCHANTS = [
    "Target",
    "Lowe's",
    "BJ's Wholesale Club",
    "Publix",
    "Staples",
    "PetSmart",
    "Old Navy",
    "Marshalls",
    "TJ Maxx",
    "Ross",
    "Dollar Tree",
    "Five Below",
    "Shoe Carnival",
    "Burlington",
    "DSW",
    "Mattress Firm",
    "Beauty Master",
]

_CONDITION_PATTERNS = [
    (
        re.compile(
            r"\bloyalty\b|\bwith card\b|\bmembers?\b|"
            r"\brewards?\b|\bcircle\b",
            re.IGNORECASE,
        ),
        "loyalty_card",
    ),
    (
        re.compile(
            r"\bcoupon\b|\bclip\b|\bdigital\b|"
            r"\brebate\b|\bcash back\b",
            re.IGNORECASE,
        ),
        "coupon",
    ),
    (
        re.compile(
            r"\bbuy \d|\bbogo\b|\bmust buy\b|"
            r"\bwhen you buy\b|\b2/\$|\b3/\$",
            re.IGNORECASE,
        ),
        "multi_buy",
    ),
    (
        re.compile(
            r"\blimit \d|\bwhile supplies\b|\bin-store only\b",
            re.IGNORECASE,
        ),
        "limit",
    ),
]


def detect_conditions(*texts: str) -> List[str]:
    joined = " ".join(str(t) for t in texts if t)

    return sorted({
        flag
        for pattern, flag in _CONDITION_PATTERNS
        if pattern.search(joined)
    })


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None

    try:
        number = float(
            str(value).strip().lstrip("$").replace(",", "")
        )

        if not math.isfinite(number):
            return None

        return number

    except (ValueError, TypeError):
        return None


def _first(node: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if node.get(key) is not None:
            return node[key]

    return None


def _as_date(value: Any) -> str:
    if isinstance(value, (int, float)) and value > 0:
        try:
            if value > 100000000000:
                value = value / 1000

            return datetime.fromtimestamp(
                value,
                tz=timezone.utc,
            ).strftime("%Y-%m-%d")

        except (ValueError, OSError, OverflowError):
            return ""

    if isinstance(value, str):
        text = value.strip()

        try:
            return datetime.fromisoformat(
                text.replace("Z", "+00:00")
            ).date().isoformat()

        except ValueError:
            pass

        for fmt in ("%m/%d/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(
                    text, fmt
                ).date().isoformat()

            except ValueError:
                continue

    return ""


def _safe_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""

    if value.startswith("//"):
        value = "https:" + value

    try:
        parsed = urllib.parse.urlparse(value)

        if parsed.scheme == "https" and parsed.hostname:
            return value

    except ValueError:
        pass

    return ""


def _merchant_key(value: str) -> str:
    key = re.sub(
        r"[^a-z0-9]",
        "",
        str(value).lower(),
    )

    aliases = {
        "bjs": "bjswholesaleclub",
        "bjswholesale": "bjswholesaleclub",
        "loweshomeimprovement": "lowes",
        "targetcircle": "target",
        "publixsupermarkets": "publix",
        "rossdressforless": "ross",
        "burlingtoncoatfactory": "burlington",
        "tjmaxx": "tjmaxx",
    }

    return aliases.get(key, key)


def configured_merchants() -> List[str]:
    raw = os.environ.get("FLIPP_MERCHANTS", "")

    if raw.strip():
        return [
            item.strip()
            for item in raw.split(",")
            if item.strip()
        ]

    return list(DEFAULT_MERCHANTS)


def configured_postal_codes() -> List[str]:
    raw = os.environ.get(
        "FLIPP_POSTAL_CODES",
        ",".join(DEFAULT_POSTAL_CODES),
    )

    return list(dict.fromkeys(
        item.strip()
        for item in raw.split(",")
        if re.fullmatch(r"\d{5}", item.strip())
    ))


def _today() -> str:
    return datetime.now(
        ZoneInfo("America/New_York")
    ).date().isoformat()


def _active(deal: Dict[str, Any]) -> bool:
    today = _today()

    start = deal.get("valid_from", "")
    end = deal.get("valid_to", "")

    if start and start > today:
        return False

    if end and end < today:
        return False

    return True


def parse_deal(
    raw: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    if not isinstance(raw, dict):
        return None

    name = _first(
        raw,
        "name",
        "title",
        "description",
        "item_name",
    )

    if not isinstance(name, str) or not name.strip():
        return None

    price = _as_float(_first(
        raw,
        "sale_price",
        "current_price",
        "price",
        "deal_price",
        "formatted_sale_price",
    ))

    if price is None or price < 0:
        return None

    merchant = _first(
        raw,
        "merchant",
        "store",
        "retailer",
        "merchant_name",
        "store_name",
    ) or ""

    if isinstance(merchant, dict):
        merchant = merchant.get("name", "")

    details = _first(
        raw,
        "details",
        "fine_print",
        "terms",
        "description2",
        "long_description",
    ) or ""

    valid_from = _as_date(_first(
        raw,
        "valid_from",
        "start_date",
        "valid_from_date",
        "flyer_start",
        "starts_at",
    ))

    valid_to = _as_date(_first(
        raw,
        "valid_to",
        "end_date",
        "valid_to_date",
        "flyer_end",
        "expires_at",
        "ends_at",
    ))

    original = _as_float(_first(
        raw,
        "original_price",
        "regular_price",
        "was_price",
        "pre_price",
    ))

    if original is not None and original <= price:
        original = None

    saving = None
    discount = None

    if original is not None and original > 0:
        saving = round(original - price, 2)

        discount = round(
            ((original - price) / original) * 100,
            1,
        )

    image = _safe_url(_first(
        raw,
        "image_url",
        "image",
        "thumbnail_url",
        "clipping_image_url",
        "cutout_image_url",
    ))

    product_url = _safe_url(_first(
        raw,
        "url",
        "product_url",
        "merchant_url",
        "web_url",
    ))

    identity = "|".join([
        _merchant_key(str(merchant)),
        name.strip().lower(),
        str(price),
        valid_from,
        valid_to,
        str(details),
    ])

    deal_id = hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()[:24]

    return {
        "id": deal_id,
        "name": name.strip(),
        "price": price,
        "original_price": original,
        "saving": saving,
        "discount_percent": discount,
        "merchant": str(merchant).strip(),
        "details": str(details),
        "valid_from": valid_from,
        "valid_to": valid_to,
        "image_url": image,
        "product_url": product_url,
        "conditions": detect_conditions(
            name,
            str(details),
        ),
        "source": "Flipp",
        "branch_verified": False,
    }


def parse_search_payload(
    payload: Any
) -> List[Dict[str, Any]]:

    if isinstance(payload, list):
        items = payload

    elif isinstance(payload, dict):
        items = []

        for key in ("items", "results", "deals", "data"):
            node = payload.get(key)

            if isinstance(node, list):
                items = node
                break

            if isinstance(node, dict):
                nested = parse_search_payload(node)

                if nested:
                    return nested

    else:
        return []

    deals = []

    for raw in items:
        deal = parse_deal(raw)

        if deal is not None:
            deals.append(deal)

    return deals


class FlippDealsConnector(BaseConnector):

    source_type = "flyer"
    tool_name = "flyer_connector"

    def __init__(
        self,
        postal_code: Optional[str] = None,
        merchants: Optional[List[str]] = None,
        cache_path: Optional[str] = None,
        replay_only: bool = False,
        base: Optional[str] = None,
        min_interval_s: float = 1.0,
        timeout: int = 15,
    ) -> None:

        super().__init__(
            min_interval_s=min_interval_s
        )

        self.postal_code = (
            postal_code
            or os.environ.get(
                "FLIPP_POSTAL_CODE",
                "30344",
            )
        )

        self.merchants = (
            merchants
            if merchants is not None
            else configured_merchants()
        )

        cache_dir = os.environ.get(
            "FLIPP_CACHE_DIR",
            "cache",
        )

        self.cache_path = (
            cache_path
            or os.environ.get("FLIPP_CACHE_PATH")
            or os.path.join(
                cache_dir,
                f"flipp_{self.postal_code}.json",
            )
        )

        self.replay_only = (
            replay_only
            or os.environ.get(
                "FLIPP_REPLAY_ONLY", ""
            ) == "1"
        )

        self.base = (
            base
            or os.environ.get("FLIPP_API_BASE")
            or FLIPP_BASE
        ).rstrip("/")

        self.timeout = timeout

        self.refresh_hours = max(
            1,
            int(os.environ.get(
                "DEALS_REFRESH_HOURS",
                "6",
            )),
        )

        self._memory_cache = None
        self._memory_time = 0.0

    @property
    def configured(self) -> bool:
        return bool(
            re.fullmatch(
                r"\d{5}",
                self.postal_code,
            )
        )

    def _search_url(self, query: str) -> str:
        params = {
            "q": query,
            "postal_code": self.postal_code,
            "locale": "en-us",
        }

        return (
            f"{self.base}/flipp/items/search?"
            + urllib.parse.urlencode(params)
        )

    def _search(self, query: str):
        self._polite_wait()

        request = urllib.request.Request(
            self._search_url(query),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=self.timeout,
        ) as response:

            payload = json.loads(
                response.read().decode("utf-8")
            )

        return parse_search_payload(payload)

    @staticmethod
    def _merchant_matches(
        deal_merchant: str,
        store: str,
    ) -> bool:

        left = _merchant_key(deal_merchant)
        right = _merchant_key(store)

        return bool(left and right and left == right)

    def _allowed(self, deal):
        return any(
            self._merchant_matches(
                deal.get("merchant", ""),
                merchant,
            )
            for merchant in self.merchants
        )

    def _fetch_week(self):
        """Retrieve available regional flyer items."""

        deals = {}
        successful = False

        try:
            results = self._search("")
            successful = True

            for deal in results:
                if self._allowed(deal):
                    deals[deal["id"]] = deal

        except Exception as exc:
            print(
                f"Flipp {self.postal_code}: {exc}",
                flush=True,
            )

        # Supplement missing merchants with searches.
        represented = {
            _merchant_key(d["merchant"])
            for d in deals.values()
        }

        for merchant in self.merchants:

            if _merchant_key(merchant) in represented:
                continue

            try:
                results = self._search(merchant)
                successful = True

                for deal in results:
                    if self._allowed(deal):
                        deals[deal["id"]] = deal

            except Exception as exc:
                print(
                    f"Flipp {merchant}: {exc}",
                    flush=True,
                )

        if not successful:
            raise RuntimeError(
                "Flipp data source unavailable"
            )

        return list(deals.values())

    def _cache_is_fresh(self, cached):
        if cached.get("postal_code") != self.postal_code:
            return False

        try:
            fetched = datetime.fromisoformat(
                cached["fetched_at"]
            )

            if fetched.tzinfo is None:
                fetched = fetched.replace(
                    tzinfo=timezone.utc
                )

            age = (
                datetime.now(timezone.utc) - fetched
            ).total_seconds()

            return (
                0 <= age < self.refresh_hours * 3600
            )

        except (KeyError, ValueError, TypeError):
            return False

    def _load_deals(self):

        if (
            self._memory_cache is not None
            and time.time() - self._memory_time
            < self.refresh_hours * 3600
        ):
            return self._memory_cache

        cached = {}

        try:
            with open(
                self.cache_path,
                "r",
                encoding="utf-8",
            ) as handle:

                cached = json.load(handle)

            if cached.get(
                "postal_code"
            ) != self.postal_code:

                cached = {}

        except (OSError, ValueError):
            cached = {}

        if self._cache_is_fresh(cached):
            deals = cached.get("deals", [])

        elif self.replay_only:
            deals = cached.get("deals", [])

        else:
            try:
                deals = self._fetch_week()

                directory = os.path.dirname(
                    self.cache_path
                ) or "."

                os.makedirs(
                    directory,
                    exist_ok=True,
                )

                temp_path = self.cache_path + ".tmp"

                with open(
                    temp_path,
                    "w",
                    encoding="utf-8",
                ) as handle:

                    json.dump({
                        "postal_code": self.postal_code,
                        "fetched_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                        "deals": deals,
                    }, handle)

                os.replace(
                    temp_path,
                    self.cache_path,
                )

            except Exception:
                if not cached:
                    raise

                deals = cached.get("deals", [])

        self._memory_cache = deals
        self._memory_time = time.time()

        return deals

    def list_deals(self):
        """Public deals independent of receipts."""

        if not self.configured:
            return []

        results = []

        for deal in self._load_deals():

            if not self._allowed(deal):
                continue

            if not _active(deal):
                continue

            # Public feed requires a known expiry.
            if not deal.get("valid_to"):
                continue

            item = dict(deal)

            item["postal_code"] = self.postal_code
            item["branch_verified"] = False

            if not item.get("id"):
                item["id"] = hashlib.sha256(
                    json.dumps(
                        deal,
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()[:24]

            results.append(item)

        return results

    def fetch(
        self,
        product: Product,
        store: str,
        prices: PriceTable,
        fetched_at: Optional[str] = None,
    ) -> Optional[PriceObservation]:

        if not self.configured:
            return None

        try:
            deals = self._load_deals()

        except Exception:
            return None

        if not any(
            self._merchant_matches(m, store)
            for m in self.merchants
        ):
            return None

        threshold = max(
            get_config().confidence_threshold,
            STRICT_MATCH_FLOOR,
        )

        best = None
        best_score = 0.0

        for deal in deals:

            if not _active(deal):
                continue

            if not self._merchant_matches(
                deal["merchant"],
                store,
            ):
                continue

            _, score = product_match.match_product(
                deal["name"],
                "",
                [product],
            )

            if score > best_score:
                best_score = score
                best = deal

        if best is None or best_score < threshold:
            return None

        try:
            unit, base = unit_price.normalize(
                best["price"],
                product.size_value,
                product.size_unit,
            )

            confidence = 0.75

        except ValueError:
            unit = round(best["price"], 4)
            base = "unparsed"
            confidence = 0.4

        stamp = (
            fetched_at
            or datetime.now(timezone.utc).isoformat()
        )

        return PriceObservation(
            observation_id=(
                f"{store}:{product.product_id}:"
                f"{self.source_type}:{stamp}"
            ),
            product_id=product.product_id,
            store=store,
            price=round(best["price"], 4),
            unit_price=unit,
            unit=base,
            source_type=self.source_type,
            fetched_at=stamp,
            confidence=confidence,
            valid_from=best["valid_from"],
            valid_to=best["valid_to"],
            conditions=list(best["conditions"]),
        )


def flipp_registry(
    postal_code: Optional[str] = None,
    merchants: Optional[List[str]] = None,
    replay_only: bool = False,
    min_interval_s: float = 1.0,
) -> ConnectorRegistry:

    registry = ConnectorRegistry(
        min_interval_s=0.0
    )

    flipp = FlippDealsConnector(
        postal_code=postal_code,
        merchants=merchants,
        replay_only=replay_only,
        min_interval_s=min_interval_s,
    )

    chain = [
        flipp,
        registry.connectors["flyer"],
        BrowserConnector(
            min_interval_s=max(
                min_interval_s,
                2.0,
            )
        ),
    ]

    registry.ordered_for_store = (
        lambda store: list(chain)
    )

    return registry