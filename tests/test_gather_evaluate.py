"""Gather-Evaluate inner loop: verify, retry, or mark unverified."""

import unittest
from datetime import datetime, timezone

from smartpantry.agents import evaluate, gather
from smartpantry.guardrails import GuardrailService
from smartpantry.schemas import PriceObservation
from smartpantry.tools.store_connectors import ConnectorRegistry
from tests.helpers import make_store

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
STAMP = "2026-11-02T12:00:00+00:00"
PRICES = {"Northmart": {"milk-gal": 4.29, "coffee-12oz": 8.99}}


def _obs(product_id="milk-gal", store="Northmart", price=4.29,
         source="api", fetched_at=STAMP, confidence=0.9, obs_id="o1",
         valid_to=""):
    return PriceObservation(
        observation_id=obs_id, product_id=product_id, store=store,
        price=price, unit_price=round(price, 4), unit="oz",
        source_type=source, fetched_at=fetched_at, confidence=confidence,
        valid_to=valid_to)


class GatherEvaluateTest(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.guards = GuardrailService()
        self.registry = ConnectorRegistry()

    def test_gather_uses_first_source(self):
        result = gather.run(["milk-gal"], ["Northmart"], PRICES, self.store,
                            self.guards, "run-test", self.registry,
                            fetched_at=STAMP)
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["observations"][0].source_type, "api")
        self.assertEqual(result["misses"], [])

    def test_gather_miss_when_no_price(self):
        result = gather.run(["milk-gal"], ["Northmart"], {}, self.store,
                            self.guards, "run-test", self.registry)
        self.assertEqual(result["observations"], [])
        self.assertEqual(len(result["misses"]), 1)

    def test_gather_skips_tried_sources(self):
        result = gather.run(
            ["milk-gal"], ["Northmart"], PRICES, self.store, self.guards,
            "run-test", self.registry, skip_sources={"milk-gal@Northmart": {"api"}},
            fetched_at=STAMP)
        self.assertEqual(result["observations"][0].source_type, "flyer")

    def test_evaluate_verifies_stable_price(self):
        result = evaluate.run([_obs()], self.store, self.guards, "run-test",
                              NOW)
        self.assertEqual(len(result["verified"]), 1)
        self.assertEqual(result["retry_requests"], [])

    def test_evaluate_requests_second_source_on_spike(self):
        result = evaluate.run([_obs(price=6.99)], self.store, self.guards,
                              "run-test", NOW)
        self.assertEqual(result["verified"], [])
        self.assertEqual(len(result["retry_requests"]), 1)
        self.assertIn("second source", result["retry_requests"][0]["reason"])

    def test_second_source_corroboration_verifies(self):
        first = _obs(price=6.99, source="api", obs_id="o1")
        second = _obs(price=6.99, source="flyer", obs_id="o2")
        result = evaluate.run([first, second], self.store, self.guards,
                              "run-test", NOW)
        self.assertEqual(len(result["verified"]), 2)
        self.assertEqual(result["retry_requests"], [])
        self.assertEqual(result["anomalies"][0]["kind"], "spike")

    def test_stale_price_retries(self):
        old = _obs(source="flyer", fetched_at="2026-10-20T12:00:00+00:00")
        result = evaluate.run([old], self.store, self.guards, "run-test", NOW)
        self.assertEqual(result["verified"], [])
        self.assertEqual(result["retry_requests"][0]["reason"], "stale price")

    def test_low_confidence_goes_to_review(self):
        low = _obs(confidence=0.3)
        result = evaluate.run([low], self.store, self.guards, "run-test", NOW)
        self.assertEqual(result["verified"], [])
        self.assertEqual(len(result["review"]), 1)

    def test_expired_deal_dropped_not_verified(self):
        old = _obs(source="flyer", valid_to="2026-10-01")
        result = evaluate.run([old], self.store, self.guards, "run-test", NOW)
        self.assertEqual(result["verified"], [])
        self.assertEqual(result["retry_requests"], [])
        self.assertEqual(len(result["expired"]), 1)

    def test_current_deal_passes_expiry(self):
        deal = _obs(source="flyer", valid_to="2026-11-08")
        result = evaluate.run([deal], self.store, self.guards, "run-test",
                              NOW)
        self.assertEqual(len(result["verified"]), 1)
        self.assertEqual(result["expired"], [])


if __name__ == "__main__":
    unittest.main()
