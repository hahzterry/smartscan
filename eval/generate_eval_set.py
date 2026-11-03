"""Generate the labelled eval receipt set (deterministic, seed 42).

Writes eval/eval_receipts.json: 50 synthetic receipts in the scaffold
text format, each with expected line items. Synthetic data doubles as
the Contributor-tier-safe corpus for development.
"""

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smartpantry.demo import load_seed  # noqa: E402

EVAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "eval_receipts.json")


def main() -> None:
    seed = load_seed()
    rng = random.Random(42)
    products = seed["products"]
    entries = []
    for number in range(1, 51):
        picked = rng.sample(products, rng.randint(3, 8))
        text_lines = []
        expected = []
        for item in picked:
            qty = rng.randint(1, 3)
            total = round(item["base_price"] * qty, 2)
            size = "%g %s" % (item["size_value"], item["size_unit"])
            description = "%s %s" % (item["brand"], item["canonical_name"])
            text_lines.append("%d x %s (%s) @ $%.2f"
                              % (qty, description, size, total))
            expected.append({"description": description, "quantity": qty,
                             "price": total, "size_text": size})
        entries.append({"id": "eval-%02d" % number,
                        "text": "\n".join(text_lines), "expected": expected})
    with open(EVAL_PATH, "w", encoding="utf-8") as handle:
        json.dump(entries, handle, indent=1)
    print("wrote %d receipts to %s" % (len(entries), EVAL_PATH))


if __name__ == "__main__":
    main()
