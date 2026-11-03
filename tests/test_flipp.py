"""Flipp deals: tolerant parsing, strict matching, cache + replay."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from smartpantry.tools import flipp as flipp_mod
from smartpantry.tools.flipp import (
    FlippDealsConnector,
    detect_conditions,
    flipp_registry,
    parse_deal,
    parse_search_payload,
)
from tests.helpers import make_product


def _connector(cache_deals=None, **kwargs):
    handle, cache_path = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    if cache_deals is not None:
        with open(cache_path, "w", encoding="utf-8") as out:
            json.dump({"postal_code": "60601",
                       "fetched_at": datetime.now(timezone.utc).isoformat(),
                       "deals": cache_deals}, out)
    args = {"postal_code": "60601", "cache_path": cache_path,
            "min_interval_s": 0}
    args.update(kwargs)
    connector = FlippDealsConnector(**args)
    connector._temp_cache = cache_path
    return connector


def _deal(name="Northfarm Whole Milk", price=3.79, merchant="Mariano's",
          valid_to="2026-11-08", conditions=None):
    return {"name": name, "price": price, "original_price": 4.29,
            "merchant": merchant, "details": "", "valid_from": "2026-11-02",
            "valid_to": valid_to, "conditions": conditions or []}


class FlippParseTest(unittest.TestCase):
    def test_parse_deal_key_variants(self):
        deal = parse_deal({"title": "Milk", "current_price": "3.79",
                           "store": "Mariano's",
                           "start_date": "2026-11-02",
                           "end_date": "2026-11-08"})
        self.assertEqual(
            (deal["name"], deal["price"], deal["merchant"],
             deal["valid_from"], deal["valid_to"]),
            ("Milk", 3.79, "Mariano's", "2026-11-02", "2026-11-08"))

    def test_parse_deal_skips_priceless(self):
        self.assertIsNone(parse_deal({"name": "Milk"}))
        self.assertIsNone(parse_deal({"name": "Milk", "price": "free"}))
        self.assertIsNone(parse_deal("not a dict"))

    def test_payload_shapes(self):
        raw = {"name": "Milk", "price": 3.79, "merchant": "Mariano's"}
        self.assertEqual(len(parse_search_payload({"items": [raw]})), 1)
        self.assertEqual(len(parse_search_payload({"results": [raw]})), 1)
        self.assertEqual(len(parse_search_payload([raw])), 1)
        self.assertEqual(parse_search_payload({"nothing": []}), [])
        self.assertEqual(parse_search_payload("garbage"), [])

    def test_condition_detection(self):
        self.assertEqual(
            detect_conditions("Milk", "with loyalty card, limit 2"),
            ["limit", "loyalty_card"])
        self.assertEqual(
            detect_conditions("Buy 2 get 1", ""), ["multi_buy"])
        self.assertEqual(detect_conditions("Milk $3.79", ""), [])


class FlippConnectorTest(unittest.TestCase):
    def tearDown(self):
        for connector in getattr(self, "_connectors", []):
            try:
                os.unlink(connector._temp_cache)
            except OSError:
                pass

    def _track(self, connector):
        self._connectors = getattr(self, "_connectors", [])
        self._connectors.append(connector)
        return connector

    def test_fetch_matches_strictly(self):
        connector = self._track(_connector([_deal()]))
        obs = connector.fetch(make_product(), "Mariano's", {},
                              "2026-11-03T12:00:00+00:00")
        self.assertIsNotNone(obs)
        self.assertEqual(obs.price, 3.79)
        self.assertEqual(obs.source_type, "flyer")
        self.assertEqual(obs.valid_to, "2026-11-08")
        self.assertEqual(obs.confidence, 0.75)

    def test_vague_name_does_not_match(self):
        deal = _deal(name="Select Varieties Snacks")
        connector = self._track(_connector([deal]))
        self.assertIsNone(connector.fetch(make_product(), "Mariano's", {}))

    def test_wrong_merchant_skipped(self):
        connector = self._track(_connector([_deal()]))
        self.assertIsNone(connector.fetch(make_product(), "Costco", {}))

    def test_merchant_allowlist_gates(self):
        connector = self._track(_connector([_deal()], merchants=["Target"]))
        self.assertIsNone(connector.fetch(make_product(), "Mariano's", {}))

    def test_unconfigured_misses(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            connector = FlippDealsConnector(postal_code="",
                                           cache_path="/nonexistent.json",
                                           min_interval_s=0)
            self.assertFalse(connector.configured)
            self.assertIsNone(connector.fetch(make_product(), "Mariano's",
                                              {}))

    def test_fresh_cache_avoids_network(self):
        connector = self._track(_connector([_deal()]))
        with mock.patch.object(
                FlippDealsConnector, "_fetch_week",
                side_effect=AssertionError("must not fetch")):
            obs = connector.fetch(make_product(), "Mariano's", {})
        self.assertIsNotNone(obs)

    def test_stale_cache_refetches(self):
        connector = self._track(_connector([_deal()]))
        stale = {"postal_code": "60601",
                 "fetched_at": (datetime.now(timezone.utc)
                                - timedelta(days=9)).isoformat(),
                 "deals": []}
        with open(connector.cache_path, "w", encoding="utf-8") as out:
            json.dump(stale, out)
        with mock.patch.object(FlippDealsConnector, "_fetch_week",
                               return_value=[_deal()]) as fetcher:
            obs = connector.fetch(make_product(), "Mariano's", {})
        self.assertTrue(fetcher.called)
        self.assertIsNotNone(obs)

    def test_replay_only_never_fetches(self):
        connector = self._track(_connector(None, replay_only=True))
        os.unlink(connector.cache_path)
        with mock.patch.object(
                FlippDealsConnector, "_fetch_week",
                side_effect=AssertionError("must not fetch")):
            self.assertIsNone(connector.fetch(make_product(), "Mariano's",
                                              {}))

    def test_fetch_error_misses(self):
        connector = self._track(_connector(None))
        os.unlink(connector.cache_path)
        with mock.patch.object(FlippDealsConnector, "_fetch_week",
                               side_effect=Exception("flipp is down")):
            self.assertIsNone(connector.fetch(make_product(), "Mariano's",
                                              {}))

    def test_registry_leads_with_flipp(self):
        registry = flipp_registry(postal_code="60601")
        for store in ["Mariano's", "Anything"]:
            chain = registry.ordered_for_store(store)
            self.assertIsInstance(chain[0], FlippDealsConnector)
            self.assertEqual([c.source_type for c in chain],
                             ["flyer", "flyer", "browser"])


if __name__ == "__main__":
    unittest.main()
