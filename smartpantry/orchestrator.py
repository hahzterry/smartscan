"""Orchestrator: the A.G.E.N.T state machine (trigger → A → G ⇄ E → N → T)."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .agents import acquire, evaluate, gather, navigate, take_action
from .config import get_config
from .guardrails import GuardrailService, RetryBudget, TokenBudget
from .model_router import REAL, ModelRouter
from .schemas import RunLog, WatchlistItem
from .store import Store
from .tools.notifier import Notifier
from .tools.store_connectors import ConnectorRegistry, PriceTable

TRIGGERS = ("scheduled", "new_receipt", "on_demand")


class Orchestrator:
    def __init__(self, store: Store, guards: GuardrailService,
                 router: ModelRouter, notifier: Notifier,
                 registry: ConnectorRegistry = None) -> None:
        self.store = store
        self.guards = guards
        self.router = router
        self.notifier = notifier
        self.registry = registry or ConnectorRegistry()

    def run(self, trigger: str = "scheduled",
            receipt_sources: Sequence[Dict[str, str]] = (),
            prices: PriceTable = None,
            stores: Sequence[str] = (),
            approved_brands: Sequence[str] = (),
            week: str = None, build_cart: bool = False,
            data_class: str = REAL, ereceipt_source: str = None,
            now: Optional[datetime] = None,
            fetched_at: Optional[str] = None,
            run_id: str = None) -> Dict[str, Any]:
        if trigger not in TRIGGERS:
            raise ValueError("unknown trigger: %r" % trigger)
        run_id = run_id or "run-%s" % uuid.uuid4().hex[:8]
        now = now or datetime.now(timezone.utc)
        week = week or now.date().isoformat()
        prices = prices or {}
        budget = RetryBudget(get_config())
        tokens = TokenBudget(get_config())
        self.guards.audit(run_id, "run_started", trigger)

        purchases: List[Any] = []
        review: List[Dict[str, Any]] = []
        if receipt_sources or ereceipt_source:
            budget.record_step()
            acquired = acquire.run(
                list(receipt_sources), self.store, self.guards, self.router,
                run_id, data_class, ereceipt_source)
            purchases = acquired["purchases"]
            review.extend(acquired["review_queue"])
            self._log(run_id, "acquire", ["receipt_parser", "email_reader"],
                      tokens)

        watchlist = self._refresh_watchlist()
        if not watchlist:
            self.guards.audit(run_id, "empty_watchlist", "nothing to price yet")
            return {"run_id": run_id, "plan": None, "purchases": purchases,
                    "notes": ["nothing to price yet: add a receipt first"],
                    "verified": [], "unverified": [], "review": review,
                    "anomalies": [], "expired": [], "approval_id": None,
                    "feedback": {}}

        verified, unverified, anomalies, eval_review, expired = self._gather_loop(
            watchlist, list(stores), prices, run_id, budget, now, fetched_at)
        review.extend(eval_review)
        self._log(run_id, "gather_evaluate",
                  ["api_connector", "flyer_connector", "browser_connector",
                   "unit_price"], tokens)

        budget.record_step()
        decided = navigate.run(
            watchlist, self.store, self.guards, run_id, approved_brands,
            week, "plan-%s" % run_id,
            last_basket_total=self.store.last_basket_total() or None)
        self._log(run_id, "navigate", ["unit_price"], tokens)

        budget.record_step()
        acted = take_action.run(
            decided["plan"], self.store, self.guards, self.notifier, run_id,
            anomalies, decided["notes"], build_cart)
        self._log(run_id, "take_action",
                  ["notifier"] + (["draft_cart"] if build_cart else []), tokens)

        if tokens.over_budget:
            self.guards.audit(run_id, "token_budget_exceeded",
                              str(tokens.tokens))
        self.guards.audit(run_id, "run_finished", run_id)
        return {"run_id": run_id, "plan": decided["plan"],
                "notes": decided["notes"], "purchases": purchases,
                "verified": verified, "unverified": unverified,
                "review": review, "anomalies": anomalies, "expired": expired,
                "approval_id": acted["approval_id"],
                "feedback": acted["feedback"]}

    def _gather_loop(self, watchlist: List[WatchlistItem], stores: List[str],
                     prices: PriceTable, run_id: str, budget: RetryBudget,
                     now: datetime, fetched_at: Optional[str]
                     ) -> Tuple[List[Any], List[Dict[str, Any]],
                                List[Dict[str, Any]], List[Dict[str, Any]],
                                List[Dict[str, Any]]]:
        watch_ids = [w.product_id for w in watchlist]
        pending: Set[Tuple[str, str]] = {
            (pid, shop) for pid in watch_ids for shop in stores}
        skip: Dict[str, Set[str]] = {}
        verified_all: List[Any] = []
        unverified: List[Dict[str, Any]] = []
        anomalies_all: List[Dict[str, Any]] = []
        review_all: List[Dict[str, Any]] = []
        expired_all: List[Dict[str, Any]] = []
        while pending:
            budget.record_step()
            gathered = gather.run(
                sorted({pid for pid, _ in pending}), stores, prices,
                self.store, self.guards, run_id, self.registry, skip,
                fetched_at)
            fresh = [o for o in gathered["observations"]
                     if (o.product_id, o.store) in pending]
            evaluated = evaluate.run(fresh, self.store, self.guards,
                                     run_id, now)
            verified_all.extend(evaluated["verified"])
            review_all.extend(evaluated["review"])
            anomalies_all.extend(evaluated["anomalies"])
            expired_all.extend(evaluated["expired"])
            resolved = {(o.product_id, o.store)
                        for o in evaluated["verified"]}
            resolved |= {(r["observation"]["product_id"],
                          r["observation"]["store"])
                         for r in evaluated["review"]}
            resolved |= {(e["product_id"], e["store"])
                         for e in evaluated["expired"]}
            still_pending: Set[Tuple[str, str]] = set()
            for req in evaluated["retry_requests"]:
                key = (req["product_id"], req["store"])
                mapkey = "%s@%s" % key
                skip.setdefault(mapkey, set()).update(req["skip_sources"])
                resolved.add(key)
                if budget.allow_regather(mapkey):
                    still_pending.add(key)
                else:
                    unverified.append({
                        "product_id": key[0], "store": key[1],
                        "reason": "retry cap reached; marked unverified",
                    })
                    self.guards.audit(run_id, "retry_cap_reached", mapkey)
            for miss in gathered["misses"]:
                key = (miss.get("product_id", ""), miss.get("store", ""))
                if key in pending and key not in resolved:
                    resolved.add(key)
                    unverified.append({
                        "product_id": key[0], "store": key[1],
                        "reason": miss.get("reason", "no source had this price"),
                    })
            pending = still_pending
        return (verified_all, unverified, anomalies_all, review_all,
                expired_all)

    def _refresh_watchlist(self) -> List[WatchlistItem]:
        existing = {w.product_id: w for w in self.store.list_watchlist()}
        for product in self.store.list_products():
            purchases = self.store.purchases_for_product(product.product_id)
            if len(purchases) >= 2:
                dates = sorted(p.date for p in purchases)
                gaps = [(datetime.strptime(dates[i + 1], "%Y-%m-%d")
                         - datetime.strptime(dates[i], "%Y-%m-%d")).days
                        for i in range(len(dates) - 1)]
                positive = [g for g in gaps if g > 0]
                frequency = int(sum(positive) / len(positive)) if positive else 7
                frequency = max(1, frequency)
                target = min(p.price_paid / p.quantity
                             for p in purchases if p.quantity > 0)
                preferred = existing.get(product.product_id,
                                         WatchlistItem(product.product_id, 7,
                                                       target)).preferred_stores
                if not preferred:
                    by_store: Dict[str, int] = {}
                    for p in purchases:
                        by_store[p.store] = by_store.get(p.store, 0) + 1
                    preferred = [max(by_store, key=by_store.get)]
                self.store.save_watchlist_item(WatchlistItem(
                    product_id=product.product_id,
                    buy_frequency_days=frequency, target_price=round(target, 4),
                    preferred_stores=preferred))
        return self.store.list_watchlist()

    def _log(self, run_id: str, stage: str, tools: List[str],
             tokens: TokenBudget) -> None:
        events = ["%s: %s" % (e["event"], e["detail"])
                  for e in self.guards.audit_log if e["run_id"] == run_id]
        self.store.log_run(RunLog(
            run_id=run_id, stage=stage, tool_calls=tools,
            guardrail_events=events[-20:], tokens=tokens.tokens,
            cost=tokens.cost))
