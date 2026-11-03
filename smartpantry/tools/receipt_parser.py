"""Receipt parser: receipt photos/PDFs/text into strict line items.

Production path delegates vision to the model backend (Muse Spark 1.3
vision + strict JSON). The scaffold additionally reads a documented
plain-text format and JSON fixtures so ingestion is testable offline::

    2 x Organic Bananas (12 oz) @ $1.29

Only exact-format lines are accepted; anything else is reported as an
error and rejected, never guessed. Text is redacted before any model call.
"""

import json
import os
import re
from typing import Any, Dict, List, Tuple

from ..guardrails import redact
from ..model_router import BaseBackend
from ..schemas import ReceiptLine

_LINE_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*x\s*(.+?)\s*@\s*\$?(\d+(?:\.\d+)?)\s*$"
)
_SIZE_RE = re.compile(r"\(([^)]+)\)\s*$")

VISION_PROMPT = (
    "Transcribe this grocery receipt into strict JSON: a list of objects "
    'with keys description (string), quantity (number), price (number, line '
    "total), confidence (0-1), and size_text (string, e.g. '12 oz', or ''). "
    "Return ONLY the JSON array, no commentary."
)


def parse_receipt_text(text: str) -> Tuple[List[ReceiptLine], List[str]]:
    lines: List[ReceiptLine] = []
    errors: List[str] = []
    for lineno, raw in enumerate((text or "").splitlines(), start=1):
        if not raw.strip():
            continue
        match = _LINE_RE.match(raw)
        if not match:
            errors.append("line %d: unparseable, rejected: %r" % (lineno, raw))
            continue
        quantity = float(match.group(1))
        description = match.group(2).strip()
        price = float(match.group(3))
        size_text = ""
        size_match = _SIZE_RE.search(description)
        if size_match:
            size_text = size_match.group(1).strip()
            description = _SIZE_RE.sub("", description).strip()
        try:
            lines.append(ReceiptLine(
                description=description, quantity=quantity, price=price,
                confidence=1.0, size_text=size_text))
        except Exception as exc:  # validate() errors surface via from_dict path
            errors.append("line %d: %s" % (lineno, exc))
    return lines, errors


def parse_receipt_json(data: Any) -> Tuple[List[ReceiptLine], List[str]]:
    lines: List[ReceiptLine] = []
    errors: List[str] = []
    if not isinstance(data, list):
        return [], ["receipt JSON must be a list of line objects"]
    for index, item in enumerate(data):
        try:
            lines.append(ReceiptLine.from_dict(item))
        except (ValueError, AttributeError, TypeError) as exc:
            errors.append("line %d: %s" % (index, exc))
    return lines, errors


def parse_receipt(source: str, backend: BaseBackend,
                  data_class: str = "real") -> Tuple[List[ReceiptLine], List[str]]:
    """Parse a receipt from a file path or raw text.

    ``.json`` files load as structured lines; image/PDF paths go to the
    model backend for vision; anything else is treated as receipt text.
    """
    if os.path.exists(source) and source.lower().endswith(".json"):
        with open(source, "r", encoding="utf-8") as handle:
            return parse_receipt_json(json.load(handle))
    if os.path.exists(source):
        prompt = VISION_PROMPT + "\n\nReceipt file: %s" % redact(source)
        raw, _tokens = backend.complete(prompt, data_class)
        try:
            return parse_receipt_json(json.loads(raw))
        except ValueError:
            return [], ["vision backend did not return strict JSON"]
    if source.strip().startswith("["):
        try:
            return parse_receipt_json(json.loads(source))
        except ValueError:
            pass
    return parse_receipt_text(source)
