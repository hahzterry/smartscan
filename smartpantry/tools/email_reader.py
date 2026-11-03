"""E-receipt reader: email and online order history into receipt dicts.

The scaffold reads JSON fixtures (list of orders). A production reader
would plug in IMAP/Gmail history behind this same return shape::

    {"order_id": ..., "store": ..., "date": "YYYY-MM-DD",
     "lines": [{"description": ..., "quantity": ..., "price": ...,
                "size_text": ...}]}
"""

import json
import os
from typing import Any, Dict, List


def read_ereceipts(source: str) -> List[Dict[str, Any]]:
    """Load e-receipt orders from a JSON file or directory of JSON files."""
    paths: List[str] = []
    if os.path.isdir(source):
        for name in sorted(os.listdir(source)):
            if name.lower().endswith(".json"):
                paths.append(os.path.join(source, name))
    elif os.path.exists(source):
        paths = [source]
    else:
        raise FileNotFoundError("e-receipt source not found: %s" % source)
    orders: List[Dict[str, Any]] = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            payload = [payload]
        for order in payload:
            for key in ("order_id", "store", "date", "lines"):
                if key not in order:
                    raise ValueError(
                        "e-receipt %s missing %r" % (path, key))
            orders.append(order)
    return orders
