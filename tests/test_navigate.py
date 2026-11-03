"""Navigate: cheapest-store choice, stop maths, approved subs only."""

import unittest

from smartpantry.agents import navigate
from smartpantry.guardrails import GuardrailService
from smartpantry.schemas import PriceObservation, WatchlistItem
from tests.helpers import make_product, make_store

STAMP = "2026-11-02T12:00:00+00:00"
APPROVED = ["Northfarm", "Harvest Gold", "Green Acre"]


def _plant(store, product_id, shop, price, obs_id):
    store.add_observation(PriceObservation(
        observation_id=obs_id, product_id=product_id, store=shop,
        price=price, unit_price=round(price, 4), unit="oz",
        source_type="api", fetched_at=STAMP, confidence=0.95, verified=True))


def _watch(product_id, target, preferred):
    return WatchlistItem(product_id=product_id, buy_frequency_days=7,
                         target_price=target, preferred_stores=preferred)


class NavigateTest(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.guards = GuardrailService()
        self.store.save_product(make_product(
            "yogurt-32oz", "Plain Yogurt", "Green Acre", 32, "oz", "dairy"))
        self.store.save_product(make_product(
            "greek-yogurt-32oz", "Greek Yogurt", "Fancy Farms", 32, "oz",
            "dairy", subs=["yogurt-32oz"]))

    def _run(self, watchlist, **kwargs):
        kwargs.setdefault("approved_brands", APPROVED)
        return navigate.run(watchlist, self.store, self.guards, "run-test",
                            week="2026-11-02", plan_id="plan-test", **kwargs)

    def test_cheapest_store_wins(self):
        _plant(self.store, "milk-gal", "Northmart", 4.29, "o1")
        _plant(self.store, "milk-gal", "ValueGrocer", 3.99, "o2")
        result = self._run([_watch("milk-gal", 4.29, ["Northmart"])])
        item = result["plan"].items[0]
        self.assertEqual((item.store, item.decision), ("ValueGrocer", "switch_store"))

    def test_buy_now_at_usual_store(self):
        _plant(self.store, "coffee-12oz", "Northmart", 8.99, "o1")
        result = self._run([_watch("coffee-12oz", 8.99, ["Northmart"])])
        self.assertEqual(result["plan"].items[0].decision, "buy_now")

    def test_wait_when_far_over_target(self):
        _plant(self.store, "yogurt-32oz", "Northmart", 3.19, "o1")
        result = self._run([_watch("yogurt-32oz", 2.00, ["Northmart"])])
        self.assertEqual(result["plan"].items[0].decision, "wait")

    def test_buy_when_within_band_over_target(self):
        _plant(self.store, "yogurt-32oz", "Northmart", 3.19, "o1")
        result = self._run([_watch("yogurt-32oz", 3.00, ["Northmart"])])
        self.assertEqual(result["plan"].items[0].decision, "buy_now")

    def test_item_without_verified_price_omitted(self):
        result = self._run([_watch("yogurt-32oz", 3.00, ["Northmart"])])
        self.assertEqual(result["plan"].items, [])
        self.assertTrue(any("no verified price" in n for n in result["notes"]))

    def test_extra_stop_consolidated_when_not_worth_it(self):
        _plant(self.store, "milk-gal", "Northmart", 4.29, "o1")
        _plant(self.store, "milk-gal", "ValueGrocer", 4.50, "o2")
        _plant(self.store, "coffee-12oz", "Northmart", 9.50, "o3")
        _plant(self.store, "coffee-12oz", "ValueGrocer", 8.00, "o4")
        result = self._run([_watch("milk-gal", 5.00, ["Northmart"]),
                            _watch("coffee-12oz", 9.00, ["Northmart"])])
        stores = {i.store for i in result["plan"].items}
        self.assertEqual(stores, {"ValueGrocer"})
        self.assertTrue(any("consolidated" in n for n in result["notes"]))

    def test_two_stops_kept_when_worth_it(self):
        _plant(self.store, "milk-gal", "Northmart", 2.00, "o1")
        _plant(self.store, "milk-gal", "ValueGrocer", 9.00, "o2")
        _plant(self.store, "coffee-12oz", "Northmart", 9.00, "o3")
        _plant(self.store, "coffee-12oz", "ValueGrocer", 2.00, "o4")
        result = self._run([_watch("milk-gal", 5.00, ["Northmart"]),
                            _watch("coffee-12oz", 9.00, ["Northmart"])])
        stores = {i.store for i in result["plan"].items}
        self.assertEqual(stores, {"Northmart", "ValueGrocer"})
        self.assertTrue(any("worth it" in n for n in result["notes"]))

    def test_approved_substitute_proposed(self):
        _plant(self.store, "greek-yogurt-32oz", "Northmart", 5.49, "o1")
        _plant(self.store, "yogurt-32oz", "Northmart", 2.50, "o2")
        result = self._run([_watch("greek-yogurt-32oz", 3.00, ["Northmart"])])
        self.assertEqual(result["plan"].items[0].decision, "wait")
        self.assertTrue(any("Plain Yogurt" in n for n in result["notes"]))

    def test_unapproved_substitute_never_proposed(self):
        self.store.save_product(make_product(
            "fancy-dip-8oz", "Fancy Dip", "Fancy Farms", 8, "oz", "dairy"))
        pizza = make_product("pizza-14oz", "Frozen Pizza", "Oven Joy", 14,
                             "oz", "frozen", subs=["fancy-dip-8oz"])
        self.store.save_product(pizza)
        _plant(self.store, "pizza-14oz", "Northmart", 5.49, "o1")
        _plant(self.store, "fancy-dip-8oz", "Northmart", 0.50, "o2")
        result = self._run([_watch("pizza-14oz", 1.00, ["Northmart"])])
        self.assertEqual(result["plan"].items[0].decision, "wait")
        self.assertFalse(any("Fancy Dip" in n for n in result["notes"]))

    def test_spend_cap_flagged(self):
        _plant(self.store, "coffee-12oz", "Northmart", 300.00, "o1")
        result = self._run([_watch("coffee-12oz", 300.00, ["Northmart"])])
        self.assertTrue(any("cap" in n for n in result["notes"]))

    def test_saving_measured_against_last_basket(self):
        _plant(self.store, "milk-gal", "Northmart", 4.29, "o1")
        result = self._run(
            [_watch("milk-gal", 4.29, ["Northmart"])], last_basket_total=13.28)
        self.assertAlmostEqual(result["plan"].saving_vs_last_basket, 8.99)

    def test_ad_price_labelled_with_validity_and_conditions(self):
        self.store.add_observation(PriceObservation(
            observation_id="o1", product_id="milk-gal", store="Mariano's",
            price=3.79, unit_price=1.0, unit="L", source_type="flyer",
            fetched_at=STAMP, confidence=0.75, verified=True,
            valid_from="2026-11-02", valid_to="2026-11-08",
            conditions=["loyalty_card"]))
        result = self._run([_watch("milk-gal", 4.29, ["Mariano's"])])
        labels = [n for n in result["notes"] if "ad price" in n]
        self.assertEqual(len(labels), 1)
        self.assertIn("2026-11-08", labels[0])
        self.assertIn("loyalty card", labels[0])


if __name__ == "__main__":
    unittest.main()
