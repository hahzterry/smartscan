"""E — Evaluate: verify observations, normalise, flag anomalies."""

from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional

from ..config import get_config
from ..guardrails import GuardrailService
from ..schemas import PriceObservation
from ..store import Store

PROMPT = (
    "Match each price observation to its product, check it against price "
    "history and a second source, and score confidence. Flag spikes and "
    "fake sales; mark doubtful prices unverified rather than guessing."
)

TOOLS = frozenset({"unit_price", "product_match"})


def _corroborated(obs: PriceObservation,
                  batch: List[PriceObservation], band: float) -> bool:
    for other in batch:
        if other.observation_id == obs.observation_id:
            continue
        if (other.product_id, other.store) != (obs.product_id, obs.store):
            continue
        if other.source_type == obs.source_type:
            continue
        if obs.price <= 0:
            continue
        if abs(other.price - obs.price) / obs.price <= band:
            return True
    return False


def _deal_expired(valid_to: str, today: str) -> bool:
    if not valid_to:
        return False
    try:
        datetime.strptime(valid_to, "%Y-%m-%d")
    except ValueError:
        return False
    return valid_to < today


def run(observations: List[PriceObservation], store: Store,
        guards: GuardrailService, run_id: str,
        now: Optional[datetime] = None) -> Dict[str, Any]:
    config = get_config()
    today = (now or datetime.now(timezone.utc)).date().isoformat()
    verified: List[PriceObservation] = []
    unverified: List[Dict[str, Any]] = []
    review: List[Dict[str, Any]] = []
    anomalies: List[Dict[str, Any]] = []
    retry_requests: List[Dict[str, Any]] = []
    expired: List[Dict[str, Any]] = []
    for obs in observations:
        if _deal_expired(obs.valid_to, today):
            expired.append({"product_id": obs.product_id, "store": obs.store,
                            "reason": "deal ended %s" % obs.valid_to})
            guards.audit(run_id, "deal_expired", obs.observation_id)
            continue
        history = store.price_history(obs.product_id)
        baseline = median(history) if history else None
        if guards.is_stale(obs.fetched_at, obs.source_type, now):
            retry_requests.append({
                "product_id": obs.product_id, "store": obs.store,
                "skip_sources": [obs.source_type], "reason": "stale price",
            })
            guards.audit(run_id, "stale_price", obs.observation_id)
            continue
        moved = guards.needs_second_source(obs.price, history)
        if moved and not _corroborated(obs, observations, config.sanity_band):
            retry_requests.append({
                "product_id": obs.product_id, "store": obs.store,
                "skip_sources": [obs.source_type],
                "reason": "moved >40% from history, needs second source",
            })
            guards.audit(run_id, "second_source_required", obs.observation_id)
            continue
        if obs.confidence < config.confidence_threshold or obs.unit == "unparsed":
            review.append({"observation": obs.to_dict(),
                           "reasons": ["low confidence or unparsed unit"]})
            guards.audit(run_id, "observation_review_queued",
                         obs.observation_id)
            continue
        obs.verified = True
        store.add_observation(obs)
        verified.append(obs)
        if baseline:
            if obs.price > baseline * (1 + config.sanity_band):
                anomalies.append({"product_id": obs.product_id,
                                  "store": obs.store, "kind": "spike",
                                  "price": obs.price, "baseline": baseline})
            elif obs.price < baseline * 0.75:
                anomalies.append({"product_id": obs.product_id,
                                  "store": obs.store, "kind": "price_drop",
                                  "price": obs.price, "baseline": baseline})
    guards.audit(run_id, "evaluate_done",
                 "%d verified, %d retries, %d review, %d anomalies, %d expired"
                 % (len(verified), len(retry_requests), len(review),
                    len(anomalies), len(expired)))
    return {"verified": verified, "unverified": unverified, "review": review,
            "anomalies": anomalies, "retry_requests": retry_requests,
            "expired": expired}
