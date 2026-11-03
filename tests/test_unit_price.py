"""Unit-price maths and size parsing."""

import unittest

from smartpantry.tools.unit_price import normalize, normalize_unit, parse_size


class UnitPriceTest(unittest.TestCase):
    def test_weight_to_oz(self):
        self.assertEqual(normalize(3.00, 12, "oz"), (0.25, "oz"))
        self.assertEqual(normalize(4.00, 1, "lb"), (0.25, "oz"))

    def test_metric_weight(self):
        unit, base = normalize(35.274, 1, "kg")
        self.assertEqual(base, "oz")
        self.assertAlmostEqual(unit, 1.0, places=3)

    def test_volume_to_floz(self):
        unit, base = normalize(33.814, 1, "L")
        self.assertEqual(base, "floz")
        self.assertAlmostEqual(unit, 1.0, places=3)

    def test_count(self):
        self.assertEqual(normalize(3.48, 12, "ct"), (0.29, "ct"))

    def test_aliases_and_parse(self):
        self.assertEqual(normalize_unit("Pounds"), "lb")
        self.assertEqual(parse_size("12 oz"), (12.0, "oz"))
        self.assertEqual(parse_size("1.5 L"), (1.5, "l"))

    def test_unknown_unit_raises(self):
        with self.assertRaises(ValueError):
            normalize(4.29, 1, "gallon")
        with self.assertRaises(ValueError):
            parse_size("a lot")

    def test_zero_size_raises(self):
        with self.assertRaises(ValueError):
            normalize(4.29, 0, "oz")


if __name__ == "__main__":
    unittest.main()
