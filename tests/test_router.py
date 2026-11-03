"""Model routing: stage mapping and the Contributor data rule."""

import os
import unittest
from unittest import mock

from smartpantry.model_router import (
    REAL,
    SYNTHETIC,
    FakeBackend,
    HttpBackend,
    ModelRouter,
)


class RouterTest(unittest.TestCase):
    def setUp(self):
        self.router = ModelRouter()

    def test_stage_routing(self):
        self.assertEqual(self.router.route("acquire", REAL), "standard")
        self.assertEqual(self.router.route("evaluate", REAL), "open")

    def test_contributor_blocked_for_real_data(self):
        with self.assertRaises(ValueError):
            self.router.route("acquire", REAL, tier_override="contributor")

    def test_contributor_allowed_for_synthetic(self):
        self.assertEqual(
            self.router.route("acquire", SYNTHETIC,
                              tier_override="contributor"), "contributor")

    def test_unknown_tier_rejected(self):
        with self.assertRaises(KeyError):
            self.router.route("acquire", REAL, tier_override="telepathy")

    def test_cost_accounting(self):
        _text, tokens, cost = self.router.complete(
            "acquire", "transcribe this receipt", REAL)
        self.assertGreater(tokens, 0)
        self.assertGreater(cost, 0)
        _text, _tokens, open_cost = self.router.complete(
            "evaluate", "match this", REAL)
        self.assertEqual(open_cost, 0.0)

    def test_http_backend_needs_env(self):
        backend = HttpBackend("muse-spark-1.3")
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                backend.complete("hello", REAL)

    def test_fake_backend_counts_calls(self):
        backend = FakeBackend(canned="[]")
        text, _tokens = backend.complete("prompt", SYNTHETIC)
        self.assertEqual(text, "[]")
        self.assertEqual(backend.calls, 1)


if __name__ == "__main__":
    unittest.main()
