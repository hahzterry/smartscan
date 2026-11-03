"""Shared fixtures for the focused test suite."""

from smartpantry.schemas import Product, Purchase
from smartpantry.store import Store


def make_product(product_id="milk-gal", name="Whole Milk", brand="Northfarm",
                 size=3.79, unit="L", category="dairy", subs=None):
    return Product(product_id=product_id, canonical_name=name, brand=brand,
                   size_value=size, size_unit=unit, category=category,
                   approved_substitutes=list(subs or []))


def make_store():
    """In-memory store: milk (recurring) + coffee (single purchase)."""
    store = Store(":memory:")
    store.save_product(make_product())
    store.save_product(make_product(
        "coffee-12oz", "Ground Coffee", "Harvest Gold", 12, "oz", "pantry"))
    store.add_purchase(Purchase(
        purchase_id="p1", date="2026-10-20", store="Northmart",
        product_id="milk-gal", quantity=1, price_paid=4.29, receipt_id="r1"))
    store.add_purchase(Purchase(
        purchase_id="p2", date="2026-10-27", store="Northmart",
        product_id="milk-gal", quantity=1, price_paid=4.29, receipt_id="r2"))
    store.add_purchase(Purchase(
        purchase_id="p3", date="2026-10-27", store="Northmart",
        product_id="coffee-12oz", quantity=1, price_paid=8.99, receipt_id="r2"))
    return store
