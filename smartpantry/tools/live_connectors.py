"""Live price connectors: Walmart Affiliate API + Target RedSky.

Walmart is the official path: it needs a walmart.io affiliate account with
an approved app, a consumer ID + key version, an RSA keypair, and a
publisher ID. Every request is RSA-SHA256 signed (see ``walmart_signature``;
format per walmart.io docs: sign ``consumerID\\ntimestamp\\nkeyVersion\\n``).

Target has no public developer API, so this uses RedSky — the JSON backend
that target.com itself loads. It needs no signup but offers no contract:
it can drift without notice, and anything beyond polite personal polling
may breach Target's terms. Keep it to the daily sweep, rate-limited, one
request per item. If RedSky changes shape, items miss gracefully and land
in ``unverified`` rather than guessing.

Both connectors return ``None`` on any failure (missing credentials,
network error, unknown shape) so Gather treats it as a miss, never a crash.
"""

import base64
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..schemas import PriceObservation, Product
from . import unit_price
from .store_connectors import (
    BaseConnector,
    BrowserConnector,
    ConnectorRegistry,
    FlyerConnector,
    PriceTable,
)

WALMART_API_BASE = "https://developer.api.walmart.com"
REDSKY_BASE = "https://redsky.target.com"
USER_AGENT = "smartpantry/0.1 (personal use; 1 req/s max)"


def walmart_signature(consumer_id: str, timestamp_ms: str, key_version: str,
                      private_key_pem: str) -> str:
    """RSA-SHA256 sign the Walmart canonical string, return base64.

    Canonical form: ``{consumerID}\\n{timestamp}\\n{keyVersion}\\n`` with the
    timestamp in epoch milliseconds. Signs via the ``openssl`` CLI so the
    project stays dependency-free.
    """
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("openssl CLI is required to sign Walmart requests")
    handle, key_path = tempfile.mkstemp(prefix="walmart-key-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as key_file:
            key_file.write(private_key_pem)
        os.chmod(key_path, 0o600)
        data = "%s\n%s\n%s\n" % (consumer_id, timestamp_ms, key_version)
        proc = subprocess.run(
            [openssl, "dgst", "-sha256", "-sign", key_path, "-binary"],
            input=data.encode("utf-8"), capture_output=True, timeout=30)
        if proc.returncode != 0:
            raise RuntimeError("openssl signing failed: %s"
                               % proc.stderr.decode("utf-8", "replace"))
        return base64.b64encode(proc.stdout).decode("ascii")
    finally:
        try:
            os.unlink(key_path)
        except OSError:
            pass


def _http_get_json(url: str, headers: Dict[str, str],
                   timeout: int = 10) -> Any:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise urllib.error.URLError("HTTP %s" % response.status)
        return json.loads(response.read().decode("utf-8"))


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().lstrip("$").replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _digits(value: str) -> str:
    return "".join(c for c in value if c.isdigit())


def _make_observation(product: Product, store: str, source: str, price: float,
                      confidence: float, stamp: str, obs_id: str
                      ) -> PriceObservation:
    try:
        unit, base = unit_price.normalize(
            price, product.size_value, product.size_unit)
    except ValueError:
        unit, base, confidence = round(price, 4), "unparsed", confidence * 0.5
    return PriceObservation(
        observation_id=obs_id, product_id=product.product_id, store=store,
        price=round(price, 4), unit_price=unit, unit=base,
        source_type=source, fetched_at=stamp, confidence=confidence)


class WalmartAffiliateConnector(BaseConnector):
    """Official Walmart Affiliate (walmart.io) product API."""

    source_type = "api"
    tool_name = "api_connector"

    def __init__(self, consumer_id: Optional[str] = None,
                 key_version: Optional[str] = None,
                 private_key: Optional[str] = None,
                 publisher_id: Optional[str] = None,
                 base: Optional[str] = None,
                 min_interval_s: float = 1.0,
                 timeout: int = 10) -> None:
        super().__init__(min_interval_s=min_interval_s)
        self.consumer_id = consumer_id or os.environ.get("WALMART_CONSUMER_ID", "")
        self.key_version = key_version or os.environ.get("WALMART_KEY_VERSION", "")
        self.private_key = private_key or os.environ.get("WALMART_PRIVATE_KEY", "")
        if not self.private_key:
            key_path = os.environ.get("WALMART_PRIVATE_KEY_PATH", "")
            if key_path and os.path.exists(key_path):
                with open(key_path, "r", encoding="utf-8") as handle:
                    self.private_key = handle.read()
        self.publisher_id = publisher_id or os.environ.get(
            "WALMART_PUBLISHER_ID", "")
        self.base = (base or os.environ.get("WALMART_API_BASE")
                     or WALMART_API_BASE).rstrip("/")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.consumer_id and self.key_version
                    and self.private_key and self.publisher_id)

    def _headers(self) -> Dict[str, str]:
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        return {
            "WM_CONSUMER.ID": self.consumer_id,
            "WM_CONSUMER.INTIMESTAMP": timestamp,
            "WM_SEC.KEY_VERSION": self.key_version,
            "WM_SEC.AUTH_SIGNATURE": walmart_signature(
                self.consumer_id, timestamp, self.key_version,
                self.private_key),
            "User-Agent": USER_AGENT,
        }

    def _lookup_url(self, item_id: str) -> str:
        return ("%s/api-proxy/service/affil/product/v2/items/%s"
                "?publisherId=%s" % (
                    self.base, urllib.parse.quote(item_id, safe=""),
                    urllib.parse.quote(self.publisher_id, safe="")))

    def _search_url(self, query: str) -> str:
        return ("%s/api-proxy/service/affil/product/v2/search?query=%s"
                "&publisherId=%s" % (
                    self.base, urllib.parse.quote(query, safe=""),
                    urllib.parse.quote(self.publisher_id, safe="")))

    def fetch(self, product: Product, store: str, prices: PriceTable,
              fetched_at: Optional[str] = None) -> Optional[PriceObservation]:
        _ = prices
        if not self.configured:
            return None
        item_id = (product.external_ids.get(store)
                   or product.external_ids.get("walmart", ""))
        try:
            self._polite_wait()
            if item_id:
                payload = _http_get_json(self._lookup_url(item_id),
                                         self._headers(), self.timeout)
                node = payload.get("item", payload) if isinstance(
                    payload, dict) else {}
                price = _as_float(node.get("salePrice"))
                if price is None:
                    price = _as_float(node.get("price"))
                confidence = 0.95
            elif product.upc:
                payload = _http_get_json(self._search_url(product.upc),
                                         self._headers(), self.timeout)
                node = self._match_upc(payload, product.upc)
                if node is None:
                    return None
                price = _as_float(node.get("salePrice"))
                if price is None:
                    price = _as_float(node.get("price"))
                confidence = 0.85
            else:
                return None
            if price is None or price < 0:
                return None
            stamp = fetched_at or datetime.now(timezone.utc).isoformat()
            return _make_observation(
                product, store, self.source_type, price, confidence, stamp,
                "%s:%s:%s:%s" % (store, product.product_id,
                                 self.source_type, stamp))
        except Exception:
            return None

    @staticmethod
    def _match_upc(payload: Any, upc: str) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None
        items = payload.get("items", [])
        if not isinstance(items, list):
            return None
        want = _digits(upc)
        for item in items:
            if not isinstance(item, dict):
                continue
            got = _digits(str(item.get("upc", "")))
            if want and got and (got == want or got.endswith(want)
                                 or want.endswith(got)):
                return item
        return None


class TargetRedskyConnector(BaseConnector):
    """Target RedSky aggregations API (unofficial — see module docstring)."""

    source_type = "api"
    tool_name = "api_connector"

    def __init__(self, api_key: Optional[str] = None,
                 store_id: Optional[str] = None,
                 base: Optional[str] = None,
                 min_interval_s: float = 1.0,
                 timeout: int = 10) -> None:
        super().__init__(min_interval_s=min_interval_s)
        self.api_key = api_key or os.environ.get("TARGET_REDSKY_KEY", "")
        # pricing_store_id: your physical store for shelf prices. Without
        # it RedSky returns the online price.
        self.store_id = store_id or os.environ.get("TARGET_STORE_ID", "")
        self.base = (base or os.environ.get("TARGET_REDSKY_BASE")
                     or REDSKY_BASE).rstrip("/")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _pdp_url(self, tcin: str) -> str:
        params = {"key": self.api_key, "tcin": tcin, "is_bot": "false"}
        if self.store_id:
            params["pricing_store_id"] = self.store_id
        return ("%s/redsky_aggregations/v1/web/pdp_client_v1?%s"
                % (self.base, urllib.parse.urlencode(params)))

    def fetch(self, product: Product, store: str, prices: PriceTable,
              fetched_at: Optional[str] = None) -> Optional[PriceObservation]:
        _ = prices
        tcin = (product.external_ids.get(store)
                or product.external_ids.get("target", ""))
        if not self.configured or not tcin:
            return None
        try:
            self._polite_wait()
            payload = _http_get_json(
                self._pdp_url(tcin), {"User-Agent": USER_AGENT}, self.timeout)
            price = self._extract_price(payload)
            if price is None or price < 0:
                return None
            stamp = fetched_at or datetime.now(timezone.utc).isoformat()
            return _make_observation(
                product, store, self.source_type, price, 0.9, stamp,
                "%s:%s:%s:%s" % (store, product.product_id,
                                 self.source_type, stamp))
        except Exception:
            return None

    @staticmethod
    def _extract_price(payload: Any) -> Optional[float]:
        try:
            node = payload["data"]["product"]["price"]
        except (KeyError, TypeError):
            return None
        if not isinstance(node, dict):
            return None
        price = _as_float(node.get("current_retail"))
        if price is None:
            price = _as_float(node.get("formatted_current_price"))
        if price is None:
            price = _as_float(node.get("regular_retail"))
        return price


def live_registry(min_interval_s: float = 1.0) -> ConnectorRegistry:
    """Registry with live API connectors first for Target and Walmart.

    Unconfigured connectors return misses without touching the network,
    so this is safe to use before credentials exist — items just land
    in ``unverified`` until keys and item IDs are filled in.
    """
    registry = ConnectorRegistry(min_interval_s=min_interval_s)
    registry.register("Walmart", [
        WalmartAffiliateConnector(min_interval_s=min_interval_s),
        FlyerConnector(min_interval_s=min_interval_s),
        BrowserConnector(min_interval_s=max(min_interval_s, 2.0)),
    ])
    registry.register("Target", [
        TargetRedskyConnector(min_interval_s=min_interval_s),
        FlyerConnector(min_interval_s=min_interval_s),
        BrowserConnector(min_interval_s=max(min_interval_s, 2.0)),
    ])
    return registry
