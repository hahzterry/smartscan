"""Observability: per-run traces and the receipt eval harness."""

import json
import os
from typing import Any, Callable, Dict, List, Tuple

from .schemas import ReceiptLine


class Tracer:
    def __init__(self, directory: str = "traces") -> None:
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def path(self, run_id: str) -> str:
        return os.path.join(self.directory, "%s.jsonl" % run_id)

    def event(self, run_id: str, stage: str, message: str) -> None:
        with open(self.path(run_id), "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"stage": stage, "message": message}) + "\n")


def _lines_match(got: ReceiptLine, want: Dict[str, Any]) -> bool:
    if got.description.strip().lower() != str(want["description"]).strip().lower():
        return False
    if abs(got.quantity - float(want["quantity"])) > 1e-9:
        return False
    if abs(got.price - float(want["price"])) > 1e-9:
        return False
    want_size = str(want.get("size_text", "")).strip().lower()
    if want_size and got.size_text.strip().lower() != want_size:
        return False
    return True


ParseFn = Callable[[str], Tuple[List[ReceiptLine], List[str]]]


def run_eval(eval_path: str, parse_fn: ParseFn) -> Dict[str, Any]:
    """Run the labelled-receipt eval set through ``parse_fn``.

    Each eval entry: ``{"id": ..., "text": ..., "expected": [line dicts]}``.
    """
    with open(eval_path, "r", encoding="utf-8") as handle:
        entries = json.load(handle)
    total = 0
    matched = 0
    failures: List[Dict[str, Any]] = []
    for entry in entries:
        lines, errors = parse_fn(entry["text"])
        expected = entry["expected"]
        total += len(expected)
        for want in expected:
            if any(_lines_match(got, want) for got in lines):
                matched += 1
            else:
                failures.append({"receipt": entry["id"], "want": want,
                                 "errors": errors})
    return {"receipts": len(entries), "total_lines": total,
            "matched": matched,
            "no_touch_rate": round(matched / total, 4) if total else 0.0,
            "failures": failures}
