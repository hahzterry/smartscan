"""Orchestrator: full loop, approval gate, retry exhaustion."""

import unittest

from smartpantry.guardrails import GuardrailService
from smartpantry.model_router import FakeBackend, ModelRouter
from smartpantry.orchestrator import Orchestrator
from smartpantry.schemas import WatchlistItem
from smartpantry.store import Store
from smartpantry.tools.notifier import Notifier
from tests.helpers import make_product

RECEIPT = ("1 x Northfarm Whole Milk (3.79 L) @ $4.29\n"
           "1 x Harvest Gold Ground Coffee (12 oz) @ $8.99")
PRICES = {"Northmart": {"milk-gal": 4.29, "coffee-12oz": 8.99},
          "ValueGrocer": {"milk-gal": 4.09, "coffee-12oz": 9.29}}
STORES = ["Northmart", "ValueGrocer"]
APPROVED = ["Northfarm", "Harvest Gold"]


def _services(store=None):
    store = store or Store(":memory:")
    guards = GuardrailService()
    router = ModelRouter({"standard": FakeBackend(),
                          "contributor": FakeBackend(),
                          "open": FakeBackend()})
    notifier = Notifier(backend="memory")
    return store, guards, Orchestrator(store, guards, router, notifier)


class OrchestratorTest(unittest.TestCase):
    def setUp(self):
        self.store, self.guards, self.orch = _services()
        self.store.save_product(make_product())
        self.store.save_product(make_product(
            "coffee-12oz", "Ground Coffee", "Harvest Gold", 12, "oz",
            "pantry"))

    def _receipts(self):
        return [{"source": RECEIPT, "date": "2026-10-20", "store": "Northmart"},
                {"source": RECEIPT, "date": "2026-10-27", "store": "Northmart"}]

    def test_unknown_trigger_rejected(self):
        with self.assertRaises(ValueError):
            self.orch.run(trigger="teleport", run_id="t")

    def test_empty_run_reports_nothing_to_price(self):
        empty_store, _, orch = _services()
        result = orch.run(trigger="scheduled", prices={}, stores=STORES,
                          run_id="empty")
        self.assertIsNone(result["plan"])
        self.assertTrue(any("receipt" in n for n in result["notes"]))

    def test_full_run_receipts_to_plan(self):
        result = self.orch.run(
            trigger="new_receipt", receipt_sources=self._receipts(),
            prices=PRICES, stores=STORES, approved_brands=APPROVED,
            run_id="full")
        self.assertEqual(len(result["purchases"]), 4)
        self.assertEqual(len(result["verified"]), 4)
        self.assertEqual(result["unverified"], [])
        plan = result["plan"]
        self.assertIsNotNone(plan)
        by_id = {i.product_id: i for i in plan.items}
        # $0.20 multi-stop saving is below the $2 extra-stop cost, so the
        # optimiser consolidates everything to Northmart.
        self.assertEqual(by_id["milk-gal"].store, "Northmart")
        self.assertEqual(by_id["milk-gal"].decision, "buy_now")
        self.assertEqual(by_id["coffee-12oz"].decision, "buy_now")
        self.assertAlmostEqual(plan.expected_total, 13.28)
        self.assertAlmostEqual(plan.saving_vs_last_basket, 0.0)
        self.assertTrue(any("consolidated" in n for n in result["notes"]))
        self.assertIsNone(result["approval_id"])
        self.assertTrue(self.store.run_logs("full"))
        self.assertTrue(self.orch.notifier.sent)

    def test_cart_waits_for_explicit_approval(self):
        result = self.orch.run(
            trigger="new_receipt", receipt_sources=self._receipts(),
            prices=PRICES, stores=STORES, approved_brands=APPROVED,
            build_cart=True, run_id="cart")
        approval_id = result["approval_id"]
        self.assertIsNotNone(approval_id)
        self.assertEqual(result["plan"].approval_status, "pending")
        self.assertFalse(self.guards.approvals.is_approved(approval_id))
        self.assertTrue(result["feedback"]["cart_requested"])
        self.guards.approvals.decide(approval_id, True)
        self.assertTrue(self.guards.approvals.is_approved(approval_id))

    def test_persistent_spike_ends_unverified_not_looping(self):
        self.store.save_watchlist_item(WatchlistItem(
            product_id="milk-gal", buy_frequency_days=7, target_price=4.29,
            preferred_stores=["Northmart"]))
        spiked = {"Northmart": {"milk-gal": 6.99},
                  "ValueGrocer": {"milk-gal": 6.99}}
        result = self.orch.run(
            trigger="scheduled", prices=spiked, stores=STORES,
            approved_brands=APPROVED, run_id="spike")
        self.assertEqual(result["verified"], [])
        self.assertEqual(len(result["unverified"]), 2)
        tried = {o.source_type for o in
                 self.store.observations_for_product("milk-gal")}
        self.assertEqual(tried, {"api", "flyer", "browser"})

    def test_low_confidence_line_lands_in_review(self):
        shaky = ('[{"description": "Whole Milk", "quantity": 1, '
                 '"price": 4.29, "confidence": 0.1, "size_text": "3.79 L"}]')
        result = self.orch.run(
            trigger="new_receipt",
            receipt_sources=[{"source": shaky, "date": "2026-11-02",
                              "store": "Northmart"}],
            prices=PRICES, stores=STORES, approved_brands=APPROVED,
            run_id="review")
        self.assertEqual(len(result["review"]), 1)
        self.assertEqual(result["purchases"], [])


if __name__ == "__main__":
    unittest.main()
