"""Demo fixtures: seed catalog, fixture prices, sample receipts.

Stores and prices here are fictional. Live connectors replace
``demo_prices`` with retailer APIs, flyers, and browser fallback.
"""

import json
import os
from typing import Any, Dict, List

from .schemas import Product
from .store import Store
from .tools.store_connectors import PriceTable

SEED_PATH = os.path.join(os.path.dirname(__file__), "data", "seed_products.json")


def load_seed() -> Dict[str, Any]:
    with open(SEED_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def seed_store(store: Store, seed: Dict[str, Any] = None) -> Dict[str, Any]:
    seed = seed or load_seed()
    for item in seed["products"]:
        store.save_product(Product(
            product_id=item["product_id"],
            canonical_name=item["canonical_name"], brand=item["brand"],
            size_value=item["size_value"], size_unit=item["size_unit"],
            category=item["category"],
            approved_substitutes=list(item.get("approved_substitutes", [])),
            upc=item.get("upc", ""),
            external_ids=dict(item.get("external_ids", {}))))
    return seed


def demo_prices(seed: Dict[str, Any] = None) -> PriceTable:
    seed = seed or load_seed()
    table: PriceTable = {shop: {} for shop in seed["stores"]}
    for item in seed["products"]:
        for shop in seed["stores"]:
            multiplier = seed["multipliers"][shop]
            table[shop][item["product_id"]] = round(
                item["base_price"] * multiplier, 2)
    for deal in seed.get("deals", []):
        table[deal["store"]][deal["product_id"]] = deal["price"]
    return table


def sample_receipts() -> List[Dict[str, str]]:
    week1 = "\n".join([
        "1 x Northfarm Whole Milk (3.79 L) @ $4.29",
        "1 x Harvest Gold Large Eggs (12 ct) @ $3.49",
        "2 x Fresh Pick Bananas (1 lb) @ $1.38",
        "1 x Harvest Gold Whole Wheat Bread (20 oz) @ $3.29",
        "1 x Lakeview Chicken Breast (1 lb) @ $3.99",
        "1 x Harvest Gold Ground Coffee (12 oz) @ $8.99",
        "1 x Plain & Simple Spaghetti (16 oz) @ $1.49",
        "1 x Green Acre Baby Spinach (5 oz) @ $2.49",
        "1 x Lakeview Cheddar Cheese (8 oz) @ $2.99",
        "1 x Plain & Simple Black Beans (15 oz) @ $1.19",
    ])
    week2 = "\n".join([
        "1 x Northfarm Whole Milk (3.79 L) @ $4.29",
        "1 x Harvest Gold Large Eggs (12 ct) @ $3.49",
        "1 x Fresh Pick Bananas (1 lb) @ $0.69",
        "1 x Harvest Gold Whole Wheat Bread (20 oz) @ $3.29",
        "2 x Lakeview Chicken Breast (1 lb) @ $7.98",
        "1 x Harvest Gold Ground Coffee (12 oz) @ $8.99",
        "1 x Plain & Simple Spaghetti (16 oz) @ $1.49",
        "1 x Green Acre Baby Spinach (5 oz) @ $2.49",
        "1 x Lakeview Cheddar Cheese (8 oz) @ $2.99",
        "1 x Plain & Simple Black Beans (15 oz) @ $1.19",
        "1 x Green Acre Frozen Peas (12 oz) @ $1.79",
        "1 x Northfarm Orange Juice (52 floz) @ $4.49",
    ])
    return [{"source": week1, "date": "2026-10-20", "store": "Northmart"},
            {"source": week2, "date": "2026-10-27", "store": "Northmart"}]
