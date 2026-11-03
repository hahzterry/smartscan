"""Store connectors: retailer API, weekly flyer, browser fallback.

Priority is API first, published flyers second, browser automation last.
The scaffold serves prices from a fixture table behind the same
interface the live connectors will implement, so Gather and Evaluate
run end-to-end offline.

Live-connector rules (enforced when real fetching lands): prefer
official APIs and published flyers, respect robots.txt and site terms,
and rate-limit every source. TinyFish is the known browser-automation
option for the fallback connector.
"""

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..config import SOURCE_PRIORITY
from ..schemas import PriceObservation, Product
from . import unit_price

# (store, product_id) -> shelf price. Fixture stand-in for live sources.
PriceTable = Dict[str, Dict[str, float]]


def robots_allowed(url: str) -> bool:
    """Placeholder: a live connector must check robots.txt and terms here."""
    _ = url
    return True


class BaseConnector:
    source_type = "api"
    tool_name = "api_connector"

    def __init__(self, min_interval_s: float = 0.0) -> None:
        self.min_interval_s = min_interval_s
        self._last_fetch = 0.0

    def _polite_wait(self) -> None:
        wait = self.min_interval_s - (time.time() - self._last_fetch)
        if wait > 0:
            time.sleep(wait)
        self._last_fetch = time.time()

    def fetch(self, product: Product, store: str, prices: PriceTable,
              fetched_at: Optional[str] = None) -> Optional[PriceObservation]:
        self._polite_wait()
        price = prices.get(store, {}).get(product.product_id)
        if price is None:
            return None
        stamp = fetched_at or datetime.now(timezone.utc).isoformat()
        try:
            unit, base = unit_price.normalize(
                price, product.size_value, product.size_unit)
            confidence = 0.9
        except ValueError:
            unit, base, confidence = round(price, 4), "unparsed", 0.45
        return PriceObservation(
            observation_id="%s:%s:%s:%s" % (
                store, product.product_id, self.source_type, stamp),
            product_id=product.product_id,
            store=store,
            price=round(price, 4),
            unit_price=unit,
            unit=base,
            source_type=self.source_type,
            fetched_at=stamp,
            confidence=confidence,
        )


class ApiConnector(BaseConnector):
    source_type = "api"
    tool_name = "api_connector"


class FlyerConnector(BaseConnector):
    source_type = "flyer"
    tool_name = "flyer_connector"


class BrowserConnector(BaseConnector):
    """Last-resort fallback. Live version drives browser automation
    (TinyFish is the known option) with strict rate limits."""

    source_type = "browser"
    tool_name = "browser_connector"

    def __init__(self, min_interval_s: float = 2.0) -> None:
        super().__init__(min_interval_s=min_interval_s)


class ConnectorRegistry:
    def __init__(self, min_interval_s: float = 0.0) -> None:
        self.connectors = {
            "api": ApiConnector(min_interval_s=min_interval_s),
            "flyer": FlyerConnector(min_interval_s=min_interval_s),
            "browser": BrowserConnector(
                min_interval_s=max(min_interval_s, 0.0)),
        }
        self._overrides: Dict[str, List[BaseConnector]] = {}

    def register(self, store: str, connectors: List[BaseConnector]) -> None:
        """Pin a store to an explicit connector chain (e.g. live API first)."""
        self._overrides[store] = list(connectors)

    def ordered_for_store(self, store: str) -> List[BaseConnector]:
        if store in self._overrides:
            return list(self._overrides[store])
        return [self.connectors[name] for name in SOURCE_PRIORITY]
