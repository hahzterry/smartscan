"""Receipt parsing: exact-format text, JSON, and the vision path."""

import json
import os
import tempfile
import unittest

from smartpantry.model_router import FakeBackend
from smartpantry.tools.receipt_parser import (
    parse_receipt,
    parse_receipt_json,
    parse_receipt_text,
)


class ReceiptParserTest(unittest.TestCase):
    def test_text_format(self):
        lines, errors = parse_receipt_text(
            "2 x Organic Bananas (12 oz) @ $1.29")
        self.assertEqual(errors, [])
        self.assertEqual(lines[0].description, "Organic Bananas")
        self.assertEqual(lines[0].quantity, 2)
        self.assertEqual(lines[0].price, 1.29)
        self.assertEqual(lines[0].size_text, "12 oz")

    def test_malformed_line_rejected_not_guessed(self):
        lines, errors = parse_receipt_text("Milk $4.29\n1 x Milk @ $4.29")
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(errors), 1)

    def test_json_lines(self):
        lines, errors = parse_receipt_json(
            [{"description": "Milk", "quantity": 1, "price": 4.29,
              "confidence": 0.9, "size_text": "3.79 L"},
             {"description": "Broken"}])
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(errors), 1)

    def test_json_file(self):
        handle, path = tempfile.mkstemp(suffix=".json")
        try:
            with open(path, "w", encoding="utf-8") as out:
                json.dump([{"description": "Milk", "quantity": 1,
                            "price": 4.29, "confidence": 1.0}], out)
            lines, errors = parse_receipt(path, FakeBackend())
            self.assertEqual(len(lines), 1)
            self.assertEqual(errors, [])
        finally:
            os.unlink(path)

    def test_vision_path_uses_backend_json(self):
        handle, path = tempfile.mkstemp(suffix=".jpg")
        os.close(handle)
        try:
            backend = FakeBackend(
                canned='[{"description": "Milk", "quantity": 1, '
                       '"price": 4.29, "confidence": 0.8}]')
            lines, errors = parse_receipt(path, backend)
            self.assertEqual(len(lines), 1)
            self.assertEqual(errors, [])
            self.assertEqual(backend.calls, 1)
        finally:
            os.unlink(path)

    def test_vision_path_rejects_non_json(self):
        handle, path = tempfile.mkstemp(suffix=".jpg")
        os.close(handle)
        try:
            lines, errors = parse_receipt(path, FakeBackend(canned="nope"))
            self.assertEqual(lines, [])
            self.assertEqual(len(errors), 1)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
