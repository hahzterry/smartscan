"""A — Acquire: ingest purchases into structured line items."""

import os
from typing import Any, Dict, List

from ..config import get_config
from ..guardrails import GuardrailService, redact
from ..model_router import REAL, BaseBackend, ModelRouter
from ..schemas import Purchase
from ..store import Store
from ..tools import product_match, receipt_parser
from ..tools.email_reader import read_ereceipts

PROMPT = (
    "Extract grocery receipt line items into strict JSON. For each line "
    "give description, quantity, price (line total), confidence (0-1), and "
    "size_text. Never invent lines; use confidence below 0.7 when unsure."
)

TOOLS = frozenset({"receipt_parser", "email_reader", "redact"})


def _match_and_save(lines: List[Any], store: Store, guards: GuardrailService,
                    run_id: str, receipt_id: str, date: str, shop: str,
                    review_queue: List[Dict[str, Any]],
                    rejected: List[Dict[str, Any]]) -> List[Purchase]:
    products = store.list_products()
    threshold = get_config().confidence_threshold
    purchases: List[Purchase] = []
    for lineno, line in enumerate(lines):
        verdict, reasons = guards.check_line(line)
        if verdict == "reject":
            rejected.append({"line": line.to_dict(), "reasons": reasons})
            guards.audit(run_id, "line_rejected", "; ".join(reasons))
            continue
        product_id, confidence = product_match.match_product(
            line.description, line.size_text, products)
        if verdict == "review" or product_id is None or confidence < threshold:
            review_queue.append({
                "line": line.to_dict(), "reasons": reasons,
                "candidate": product_id, "match_confidence": confidence,
            })
            guards.audit(run_id, "line_review_queued", line.description)
            continue
        purchase = Purchase(
            purchase_id="%s-%d" % (receipt_id, lineno),
            date=date, store=shop, product_id=product_id,
            quantity=line.quantity, price_paid=line.price, receipt_id=receipt_id,
        )
        store.add_purchase(purchase)
        purchases.append(purchase)
    return purchases


def run(receipt_sources: List[Dict[str, str]], store: Store,
        guards: GuardrailService, router: ModelRouter, run_id: str,
        data_class: str = REAL,
        ereceipt_source: str = None) -> Dict[str, Any]:
    """Ingest receipts.

    ``receipt_sources``: list of ``{"source": path-or-text, "date",
    "store"}``. Returns purchases, review queue, and rejections.
    """
    backend: BaseBackend = router.backends[router.route("acquire", data_class)]
    review_queue: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    purchases: List[Purchase] = []
    for index, entry in enumerate(receipt_sources):
        guards.enforce_tool("acquire", "receipt_parser")
        raw_source = entry["source"]
        # File paths are resolved, not sent anywhere; pasted receipt text is
        # redacted before it can reach a model backend.
        text_or_path = raw_source if os.path.exists(raw_source) else redact(raw_source)
        lines, errors = receipt_parser.parse_receipt(
            text_or_path, backend, data_class)
        for err in errors:
            rejected.append({"line": None, "reasons": [err]})
            guards.audit(run_id, "line_rejected", err)
        receipt_id = "receipt-%s-%d" % (run_id, index)
        purchases.extend(_match_and_save(
            lines, store, guards, run_id, receipt_id,
            entry.get("date", "2026-11-02"), entry.get("store", "unknown"),
            review_queue, rejected))
    if ereceipt_source:
        guards.enforce_tool("acquire", "email_reader")
        for order in read_ereceipts(ereceipt_source):
            lines, errors = receipt_parser.parse_receipt_json(order["lines"])
            for err in errors:
                rejected.append({"line": None, "reasons": [err]})
            purchases.extend(_match_and_save(
                lines, store, guards, run_id, str(order["order_id"]),
                str(order["date"]), str(order["store"]),
                review_queue, rejected))
    guards.audit(run_id, "acquire_done",
                 "%d purchases, %d review, %d rejected"
                 % (len(purchases), len(review_queue), len(rejected)))
    return {"purchases": purchases, "review_queue": review_queue,
            "rejected": rejected}
