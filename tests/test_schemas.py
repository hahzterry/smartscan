"""Schemas reject bad lines; valid dicts round-trip."""

import unittest

from smartpantry.schemas import (
    Plan,
    PlanItem,
    PriceObservation,
    Product,
    ReceiptLine,
)


class SchemaTest(unittest.TestCase):
    def test_product_round_trip(self):
        product = Product.from_dict({
            "product_id": "milk-gal", "canonical_name": "Whole Milk",
            "brand": "Northfarm", "size_value": 3.79, "size_unit": "L",
            "category": "dairy", "approved_substitutes": []})
        self.assertEqual(product.to_dict()["brand"], "Northfarm")

    def test_missing_field_rejected(self):
        with self.assertRaises(ValueError):
            Product.from_dict({"product_id": "x"})

    def test_wrong_type_rejected(self):
        with self.assertRaises(ValueError):
            ReceiptLine.from_dict({"description": "Milk", "quantity": 1,
                                   "price": "free", "confidence": 1.0})

    def test_confidence_bounds_enforced(self):
        with self.assertRaises(ValueError):
            ReceiptLine.from_dict({"description": "Milk", "quantity": 1,
                                   "price": 4.29, "confidence": 1.5})

    def test_bad_source_type_rejected(self):
        with self.assertRaises(ValueError):
            PriceObservation.from_dict({
                "observation_id": "o1", "product_id": "milk-gal",
                "store": "Northmart", "price": 4.29, "unit_price": 1.13,
                "unit": "L", "source_type": "telegram",
                "fetched_at": "2026-11-02T10:00:00+00:00", "confidence": 0.9})

    def test_bad_plan_decision_rejected(self):
        with self.assertRaises(ValueError):
            Plan.from_dict({
                "plan_id": "p1", "week": "2026-11-02",
                "items": [{"product_id": "milk-gal", "store": "Northmart",
                           "expected_price": 4.29, "decision": "buy_maybe"}]})

    def test_plan_item_round_trip(self):
        item = PlanItem.from_dict({"product_id": "milk-gal",
                                   "store": "Northmart", "expected_price": 4.29,
                                   "decision": "buy_now"})
        self.assertEqual(item.decision, "buy_now")


if __name__ == "__main__":
    unittest.main()
