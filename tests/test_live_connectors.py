"""Live connectors: parsing, misses, signing, registry order.

HTTP is mocked — no test touches the network. The RSA signer is verified
against openssl itself when the CLI is present.
"""

import base64
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
from unittest import mock

from smartpantry.schemas import Product
from smartpantry.tools import live_connectors as live
from smartpantry.tools.live_connectors import (
    TargetRedskyConnector,
    WalmartAffiliateConnector,
    live_registry,
    walmart_signature,
)
from tests.helpers import make_product

STAMP = "2026-11-02T12:00:00+00:00"


def _product(**kwargs):
    product = make_product()
    for key, value in kwargs.items():
        setattr(product, key, value)
    return product


def _walmart(**kwargs):
    args = {"consumer_id": "c", "key_version": "1",
            "private_key": "PEM", "publisher_id": "pub",
            "min_interval_s": 0}
    args.update(kwargs)
    return WalmartAffiliateConnector(**args)


class WalmartConnectorTest(unittest.TestCase):
    def test_lookup_parses_sale_price(self):
        connector = _walmart()
        payload = {"item": {"itemId": "123", "name": "Milk",
                            "salePrice": 4.08}}
        with mock.patch.object(live, "_http_get_json",
                               return_value=payload) as getter, \
             mock.patch.object(live, "walmart_signature",
                               return_value="sig") as signer:
            obs = connector.fetch(_product(external_ids={"walmart": "123"}),
                                  "Walmart", {}, STAMP)
        self.assertIsNotNone(obs)
        self.assertEqual(obs.price, 4.08)
        self.assertEqual(obs.source_type, "api")
        url = getter.call_args[0][0]
        self.assertIn("/items/123", url)
        self.assertIn("publisherId=pub", url)
        self.assertEqual(getter.call_args[0][1]["WM_SEC.AUTH_SIGNATURE"],
                         "sig")
        self.assertTrue(signer.called)

    def test_lookup_falls_back_to_list_price(self):
        connector = _walmart()
        payload = {"itemId": "123", "price": "5.00"}
        with mock.patch.object(live, "_http_get_json",
                               return_value=payload), \
             mock.patch.object(live, "walmart_signature",
                               return_value="sig"):
            obs = connector.fetch(_product(external_ids={"walmart": "123"}),
                                  "Walmart", {}, STAMP)
        self.assertEqual(obs.price, 5.00)

    def test_upc_search_matches_exact_upc(self):
        connector = _walmart()
        payload = {"items": [
            {"itemId": "9", "upc": "000000000000", "salePrice": 1.00},
            {"itemId": "123", "upc": "012345678905", "salePrice": 4.08},
        ]}
        with mock.patch.object(live, "_http_get_json",
                               return_value=payload), \
             mock.patch.object(live, "walmart_signature",
                               return_value="sig"):
            obs = connector.fetch(_product(upc="012345678905"),
                                  "Walmart", {}, STAMP)
        self.assertEqual(obs.price, 4.08)

    def test_upc_search_without_match_misses(self):
        connector = _walmart()
        payload = {"items": [{"itemId": "9", "upc": "999", "salePrice": 1.0}]}
        with mock.patch.object(live, "_http_get_json",
                               return_value=payload), \
             mock.patch.object(live, "walmart_signature",
                               return_value="sig"):
            self.assertIsNone(connector.fetch(_product(upc="012345678905"),
                                              "Walmart", {}, STAMP))

    def test_unconfigured_makes_no_request(self):
        with mock.patch.object(live, "_http_get_json") as getter, \
             mock.patch.dict(os.environ, {}, clear=True):
            connector = WalmartAffiliateConnector(min_interval_s=0)
            self.assertFalse(connector.configured)
            self.assertIsNone(connector.fetch(_product(), "Walmart", {},
                                              STAMP))
            getter.assert_not_called()

    def test_no_ids_means_miss(self):
        connector = _walmart()
        with mock.patch.object(live, "_http_get_json") as getter, \
             mock.patch.object(live, "walmart_signature",
                               return_value="sig"):
            self.assertIsNone(connector.fetch(_product(), "Walmart", {},
                                              STAMP))
            getter.assert_not_called()

    def test_http_error_and_bad_shape_miss(self):
        connector = _walmart()
        product = _product(external_ids={"walmart": "123"})
        with mock.patch.object(live, "_http_get_json",
                               side_effect=urllib.error.URLError("down")), \
             mock.patch.object(live, "walmart_signature",
                               return_value="sig"):
            self.assertIsNone(connector.fetch(product, "Walmart", {}, STAMP))
        with mock.patch.object(live, "_http_get_json", return_value={}), \
             mock.patch.object(live, "walmart_signature",
                               return_value="sig"):
            self.assertIsNone(connector.fetch(product, "Walmart", {}, STAMP))

    @unittest.skipUnless(shutil.which("openssl"), "openssl CLI required")
    def test_signature_verifies_with_openssl(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_path = os.path.join(tmp, "key.pem")
            pub_path = os.path.join(tmp, "pub.pem")
            subprocess.run(["openssl", "genrsa", "-out", key_path, "2048"],
                           check=True, capture_output=True)
            subprocess.run(["openssl", "rsa", "-in", key_path,
                            "-pubout", "-out", pub_path],
                           check=True, capture_output=True)
            with open(key_path, encoding="utf-8") as handle:
                pem = handle.read()
            sig = walmart_signature("consumer-1", "1699123456789", "1", pem)
            sig_path = os.path.join(tmp, "sig.bin")
            with open(sig_path, "wb") as handle:
                handle.write(base64.b64decode(sig))
            proc = subprocess.run(
                ["openssl", "dgst", "-sha256", "-verify", pub_path,
                 "-signature", sig_path],
                input=b"consumer-1\n1699123456789\n1\n",
                capture_output=True)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("Verified OK", proc.stdout.decode())


class TargetConnectorTest(unittest.TestCase):
    def _payload(self, price_node):
        return {"data": {"product": {"tcin": "123",
                                    "price": price_node}}}

    def test_parses_current_retail(self):
        connector = TargetRedskyConnector(api_key="k", store_id="3991",
                                         min_interval_s=0)
        payload = self._payload({"current_retail": 4.08})
        with mock.patch.object(live, "_http_get_json",
                               return_value=payload) as getter:
            obs = connector.fetch(_product(external_ids={"target": "123"}),
                                  "Target", {}, STAMP)
        self.assertEqual(obs.price, 4.08)
        url = getter.call_args[0][0]
        self.assertIn("pdp_client_v1", url)
        self.assertIn("tcin=123", url)
        self.assertIn("pricing_store_id=3991", url)

    def test_falls_back_to_formatted_price(self):
        connector = TargetRedskyConnector(api_key="k", min_interval_s=0)
        payload = self._payload({"formatted_current_price": "$4.08"})
        with mock.patch.object(live, "_http_get_json",
                               return_value=payload):
            obs = connector.fetch(_product(external_ids={"target": "123"}),
                                  "Target", {}, STAMP)
        self.assertEqual(obs.price, 4.08)

    def test_missing_key_or_tcin_means_miss(self):
        with mock.patch.object(live, "_http_get_json") as getter, \
             mock.patch.dict(os.environ, {}, clear=True):
            no_key = TargetRedskyConnector(api_key="", min_interval_s=0)
            self.assertFalse(no_key.configured)
            self.assertIsNone(no_key.fetch(
                _product(external_ids={"target": "123"}), "Target", {},
                STAMP))
            keyed = TargetRedskyConnector(api_key="k", min_interval_s=0)
            self.assertIsNone(keyed.fetch(_product(), "Target", {}, STAMP))
            getter.assert_not_called()

    def test_bad_shape_and_http_error_miss(self):
        connector = TargetRedskyConnector(api_key="k", min_interval_s=0)
        product = _product(external_ids={"target": "123"})
        with mock.patch.object(live, "_http_get_json", return_value={"nope": 1}):
            self.assertIsNone(connector.fetch(product, "Target", {}, STAMP))
        with mock.patch.object(
                live, "_http_get_json",
                side_effect=urllib.error.HTTPError(
                    "http://x", 403, "Forbidden", {}, None)):
            self.assertIsNone(connector.fetch(product, "Target", {}, STAMP))


class LiveRegistryTest(unittest.TestCase):
    def test_live_chains_lead_with_live_api(self):
        registry = live_registry()
        walmart = registry.ordered_for_store("Walmart")
        self.assertIsInstance(walmart[0], WalmartAffiliateConnector)
        self.assertEqual([c.source_type for c in walmart],
                         ["api", "flyer", "browser"])
        target = registry.ordered_for_store("Target")
        self.assertIsInstance(target[0], TargetRedskyConnector)

    def test_fixture_stores_unchanged(self):
        registry = live_registry()
        chain = registry.ordered_for_store("Northmart")
        self.assertEqual(type(chain[0]).__name__, "ApiConnector")


class ProductLinkageTest(unittest.TestCase):
    def test_upc_and_external_ids_round_trip(self):
        product = Product.from_dict({
            "product_id": "milk-gal", "canonical_name": "Whole Milk",
            "brand": "Northfarm", "size_value": 3.79, "size_unit": "L",
            "category": "dairy", "upc": "012345678905",
            "external_ids": {"target": "123", "walmart": "456"}})
        self.assertEqual(product.to_dict()["upc"], "012345678905")
        self.assertEqual(product.to_dict()["external_ids"]["target"], "123")

    def test_old_dicts_still_load(self):
        product = Product.from_dict({
            "product_id": "milk-gal", "canonical_name": "Whole Milk",
            "brand": "Northfarm", "size_value": 3.79, "size_unit": "L",
            "category": "dairy"})
        self.assertEqual(product.upc, "")
        self.assertEqual(product.external_ids, {})

    def test_bad_linkage_rejected(self):
        with self.assertRaises(ValueError):
            Product.from_dict({
                "product_id": "milk-gal", "canonical_name": "Whole Milk",
                "brand": "Northfarm", "size_value": 3.79, "size_unit": "L",
                "category": "dairy", "external_ids": {"target": 123}})


if __name__ == "__main__":
    unittest.main()
