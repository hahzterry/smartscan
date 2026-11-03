"""Tracing and the labelled-receipt eval harness."""

import json
import os
import tempfile
import unittest

from smartpantry.observability import Tracer, run_eval
from smartpantry.tools.receipt_parser import parse_receipt_text


class ObservabilityTest(unittest.TestCase):
    def test_tracer_appends_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracer = Tracer(tmp)
            tracer.event("run-1", "acquire", "parsed 3 lines")
            with open(tracer.path("run-1"), encoding="utf-8") as handle:
                entry = json.loads(handle.read().strip())
            self.assertEqual(entry["stage"], "acquire")

    def _eval_file(self, entries):
        handle, path = tempfile.mkstemp(suffix=".json")
        with open(path, "w", encoding="utf-8") as out:
            json.dump(entries, out)
        self.addCleanup(os.unlink, path)
        return path

    def test_eval_perfect_score(self):
        path = self._eval_file([{
            "id": "r1", "text": "1 x Milk (3.79 L) @ $4.29",
            "expected": [{"description": "Milk", "quantity": 1,
                          "price": 4.29, "size_text": "3.79 L"}]}])
        report = run_eval(path, parse_receipt_text)
        self.assertEqual(report["no_touch_rate"], 1.0)
        self.assertEqual(report["failures"], [])

    def test_eval_reports_misses(self):
        path = self._eval_file([{
            "id": "r1", "text": "1 x Milk (3.79 L) @ $4.29",
            "expected": [{"description": "Milk", "quantity": 1,
                          "price": 4.29, "size_text": "3.79 L"},
                         {"description": "Eggs", "quantity": 1,
                          "price": 3.49, "size_text": "12 ct"}]}])
        report = run_eval(path, parse_receipt_text)
        self.assertEqual(report["no_touch_rate"], 0.5)
        self.assertEqual(len(report["failures"]), 1)


if __name__ == "__main__":
    unittest.main()
