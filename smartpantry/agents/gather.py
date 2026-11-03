"""G — Gather: fetch current prices for watchlist items on schedule."""

from typing import Any, Dict, List, Optional, Set

from ..guardrails import GuardrailService
from ..schemas import PriceObservation
from ..store import Store
from ..tools.store_connectors import ConnectorRegistry, PriceTable

PROMPT = (
    "Pick the cheapest reliable source per item (retailer API, then weekly "
    "flyer, then browser fallback) and parse the fetched result into price "
    "observations with source and timestamp."
)

TOOLS = frozenset({"api_connector", "flyer_connector", "browser_connector"})


def run(watchlist_ids: List[str], stores: List[str], prices: PriceTable,
        store: Store, guards: GuardrailService, run_id: str,
        registry: ConnectorRegistry = None,
        skip_sources: Dict[str, Set[str]] = None,
        fetched_at: Optional[str] = None) -> Dict[str, Any]:
    """Fetch one observation per item-store from the first working source.

    ``skip_sources`` maps ``"product_id@store"`` to source types already
    tried (used by the Gather-Evaluate retry loop).
    """
    registry = registry or ConnectorRegistry()
    skip_sources = skip_sources or {}
    observations: List[PriceObservation] = []
    misses: List[Dict[str, str]] = []
    for product_id in watchlist_ids:
        product = store.get_product(product_id)
        if product is None:
            misses.append({"product_id": product_id, "reason": "unknown product"})
            continue
        for shop in stores:
            key = "%s@%s" % (product_id, shop)
            skipped = skip_sources.get(key, set())
            found: Optional[PriceObservation] = None
            for connector in registry.ordered_for_store(shop):
                if connector.source_type in skipped:
                    continue
                guards.enforce_tool("gather", connector.tool_name)
                found = connector.fetch(product, shop, prices, fetched_at)
                if found is not None:
                    break
            if found is None:
                misses.append({"product_id": product_id, "store": shop,
                               "reason": "no source had this price"})
                guards.audit(run_id, "gather_miss", key)
            else:
                store.add_observation(found)
                observations.append(found)
    guards.audit(run_id, "gather_done",
                 "%d observations, %d misses"
                 % (len(observations), len(misses)))
    return {"observations": observations, "misses": misses}
