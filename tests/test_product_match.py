"""Cross-store product matching scores name, size, and brand."""

import unittest

from smartpantry.tools.product_match import match_product
from tests.helpers import make_product


class ProductMatchTest(unittest.TestCase):
    def setUp(self):
        self.catalog = [
            make_product(),
            make_product("coffee-12oz", "Ground Coffee", "Harvest Gold",
                         12, "oz", "pantry"),
        ]

    def test_exact_match_scores_high(self):
        product_id, confidence = match_product(
            "Northfarm Whole Milk", "3.79 L", self.catalog)
        self.assertEqual(product_id, "milk-gal")
        self.assertGreaterEqual(confidence, 0.7)

    def test_variant_size_still_matches(self):
        product_id, confidence = match_product(
            "Whole Milk", "1 gal", self.catalog)
        self.assertEqual(product_id, "milk-gal")
        self.assertGreater(confidence, 0.3)

    def test_wrong_item_scores_low(self):
        _pid, confidence = match_product(
            "Motor Oil", "1 qt", self.catalog)
        self.assertLess(confidence, 0.5)

    def test_conflicting_brand_penalised(self):
        _pid, confidence = match_product(
            "Harvest Gold Whole Milk", "3.79 L", self.catalog)
        _pid2, plain = match_product(
            "Whole Milk", "3.79 L", self.catalog)
        self.assertLess(confidence, plain)

    def test_empty_catalog(self):
        self.assertEqual(match_product("Milk", "1 L", []), (None, 0.0))


if __name__ == "__main__":
    unittest.main()
