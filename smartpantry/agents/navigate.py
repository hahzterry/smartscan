"""N — Navigate: decide per item where and when to buy."""

from typing import Any, Dict, List, Optional, Sequence

from ..config import get_config
from ..guardrails import GuardrailService
from ..schemas import Plan, PlanItem, PriceObservation, WatchlistItem
from ..store import Store

PROMPT = (
    "Optimise the whole basket across stores: per item choose buy now, "
    "wait, or switch store. Weigh whether an extra stop is worth the "
    "saving, and propose substitutions only from approved brands."
)

TOOLS = frozenset({"unit_price"})


def _latest_per_store(
    observations: List[PriceObservation],
) -> Dict[str, PriceObservation]:
    latest: Dict[str, PriceObservation] = {}
    for obs in sorted(observations, key=lambda o: o.fetched_at):
        latest[obs.store] = obs
    return latest


def run(watchlist: Sequence[WatchlistItem], store: Store,
        guards: GuardrailService, run_id: str,
        approved_brands: Sequence[str], week: str, plan_id: str,
        extra_stop_cost: float = 2.0,
        last_basket_total: Optional[float] = None) -> Dict[str, Any]:
    config = get_config()
    notes: List[str] = []
    items: List[PlanItem] = []
    by_store: Dict[str, Dict[str, float]] = {}
    for entry in watchlist:
        guards.enforce_tool("navigate", "unit_price")
        product = store.get_product(entry.product_id)
        if product is None:
            notes.append("skipped unknown product %s" % entry.product_id)
            continue
        latest = _latest_per_store(
            store.observations_for_product(entry.product_id, verified_only=True))
        if not latest:
            notes.append("no verified price for %s; left out of plan"
                         % product.canonical_name)
            guards.audit(run_id, "no_verified_price", entry.product_id)
            continue
        for shop, obs in latest.items():
            by_store.setdefault(shop, {})[entry.product_id] = obs.price
        cheapest = min(latest.values(), key=lambda o: o.price)
        usual = entry.preferred_stores[0] if entry.preferred_stores else None
        at_usual = usual is None or cheapest.store == usual
        if cheapest.price <= entry.target_price:
            decision = "buy_now" if at_usual else "switch_store"
        elif cheapest.price > entry.target_price * (1 + config.sanity_band):
            decision = "wait"
        else:
            decision = "buy_now" if at_usual else "switch_store"
        items.append(PlanItem(product_id=entry.product_id, store=cheapest.store,
                              expected_price=cheapest.price, decision=decision))
        if decision == "wait":
            _note_substitutions(entry, product.approved_substitutes, store,
                                guards, approved_brands, cheapest.price, notes)
    buy_items = [i for i in items if i.decision != "wait"]
    expected_total = round(sum(i.expected_price for i in buy_items), 2)
    usual_by_product = {
        entry.product_id: (entry.preferred_stores[0]
                           if entry.preferred_stores else None)
        for entry in watchlist
    }
    _consolidate_stops(buy_items, by_store, extra_stop_cost, notes,
                       usual_by_product)
    expected_total = round(sum(i.expected_price for i in buy_items), 2)
    for item in buy_items:
        latest = _latest_per_store(
            store.observations_for_product(item.product_id, verified_only=True))
        obs = latest.get(item.store)
        if obs is not None and obs.source_type == "flyer":
            product = store.get_product(item.product_id)
            name = product.canonical_name if product else item.product_id
            label = "%s at %s is this week's ad price" % (name, item.store)
            if obs.valid_to:
                label += " (ends %s)" % obs.valid_to
            if obs.conditions:
                label += "; needs %s" % ", ".join(
                    c.replace("_", " ") for c in obs.conditions)
            notes.append(label)
    if last_basket_total is None:
        last_basket_total = store.last_basket_total()
    saving = round(last_basket_total - expected_total, 2) if last_basket_total else 0.0
    if not guards.check_spend(expected_total):
        notes.append("plan total $%.2f exceeds weekly cap $%.2f; cart will "
                     "need explicit approval" % (expected_total,
                                                 config.weekly_spend_cap))
        guards.audit(run_id, "spend_cap_exceeded", str(expected_total))
    plan = Plan(plan_id=plan_id, week=week, items=items,
                expected_total=expected_total,
                saving_vs_last_basket=saving)
    errors = plan.validate()
    if errors:
        raise ValueError("built an invalid plan: %s" % "; ".join(errors))
    guards.audit(run_id, "navigate_done",
                 "%d items across %d stores, total %.2f"
                 % (len(buy_items), len({i.store for i in buy_items}),
                    expected_total))
    return {"plan": plan, "notes": notes}


def _note_substitutions(entry: WatchlistItem, substitutes: Sequence[str],
                        store: Store, guards: GuardrailService,
                        approved_brands: Sequence[str], current_best: float,
                        notes: List[str]) -> None:
    for sub_id in substitutes:
        sub = store.get_product(sub_id)
        if sub is None:
            continue
        if not guards.check_brand(sub.brand, approved_brands):
            continue
        latest = _latest_per_store(
            store.observations_for_product(sub_id, verified_only=True))
        if not latest:
            continue
        cheapest = min(latest.values(), key=lambda o: o.price)
        if cheapest.price < current_best:
            notes.append("consider approved substitute %s (%s) at %s for "
                         "$%.2f" % (sub.canonical_name, sub.brand,
                                     cheapest.store, cheapest.price))


def _consolidate_stops(buy_items: List[PlanItem],
                       by_store: Dict[str, Dict[str, float]],
                       extra_stop_cost: float, notes: List[str],
                       usual_by_product: Dict[str, str] = None) -> None:
    stores_used = {i.store for i in buy_items}
    if len(stores_used) < 2:
        return
    multi_total = sum(i.expected_price for i in buy_items)
    candidates = {}
    wanted = {i.product_id for i in buy_items}
    for shop, prices in by_store.items():
        if wanted.issubset(prices):
            candidates[shop] = sum(prices[pid] for pid in wanted)
    if not candidates:
        notes.append("no single store carries the whole basket; "
                     "%d stops needed" % len(stores_used))
        return
    best_shop = min(candidates, key=candidates.get)
    extra = len(stores_used) - 1
    if candidates[best_shop] - multi_total < extra_stop_cost * extra:
        for item in buy_items:
            item.store = best_shop
            item.expected_price = by_store[best_shop][item.product_id]
            usual = (usual_by_product or {}).get(item.product_id)
            if usual is None or best_shop == usual:
                item.decision = "buy_now"
            else:
                item.decision = "switch_store"
        notes.append("consolidated to %s: extra stop saving $%.2f below "
                     "cost $%.2f" % (best_shop, candidates[best_shop]
                                     - multi_total, extra_stop_cost * extra))
    else:
        notes.append("%d stops worth it: saving $%.2f over single store"
                     % (len(stores_used),
                        candidates[best_shop] - multi_total))
