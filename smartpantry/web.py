"""Local demo server: upload receipts, see SmartCart results, approve cart.

Single-threaded stdlib HTTP keeps demo state (approvals, audit) in one
process. JSON API:

- ``GET /`` — the SmartPantry page.
- ``GET /api/health`` — ``{"ok": true}``.
- ``POST /api/ingest`` — multipart files; runs the full pipeline with a
  draft cart and returns SmartCart rows, totals, notes, and ``approval_id``.
- ``POST /api/approve`` — ``{"approval_id": ..., "approved": bool}``.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from . import demo as demo_fixtures
from .guardrails import GuardrailService
from .inbox import parse_filename
from .model_router import FakeBackend, ModelRouter
from .orchestrator import Orchestrator
from .store import Store
from .tools.flipp import DEFAULT_MERCHANTS, flipp_registry
from .tools.notifier import Notifier

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def parse_multipart(body: bytes, boundary: bytes):
    """Split a multipart body into (field, filename, content) tuples."""
    files = []
    for chunk in body.split(b"--" + boundary):
        if chunk in (b"", b"--", b"--\r\n", b"--\n"):
            continue
        head, sep, content = chunk.partition(b"\r\n\r\n")
        if not sep:
            head, sep, content = chunk.partition(b"\n\n")
        if not sep:
            continue
        if content.endswith(b"\r\n"):
            content = content[:-2]
        elif content.endswith(b"\n"):
            content = content[:-1]
        field, filename = "", ""
        for line in head.decode("utf-8", "replace").splitlines():
            if "content-disposition" not in line.lower():
                continue
            for part in line.split(";"):
                part = part.strip()
                if part.startswith("name="):
                    field = part[5:].strip('"')
                elif part.startswith("filename="):
                    filename = os.path.basename(part[9:].strip('"'))
        if filename:
            files.append((field, filename, content))
    return files


class App:
    def __init__(self, db_path, inbox_dir, live=False, replay_only=False,
                 stores=None):
        self.inbox_dir = inbox_dir
        os.makedirs(inbox_dir, exist_ok=True)
        self.live = live
        self.store = Store(db_path)
        if not self.store.list_products():
            demo_fixtures.seed_store(self.store)
        self.guards = GuardrailService()
        self.router = ModelRouter({"standard": FakeBackend(),
                                   "contributor": FakeBackend(),
                                   "open": FakeBackend()})
        self.notifier = Notifier(backend="memory")
        registry = flipp_registry(replay_only=replay_only) if live else None
        if registry is None:
            self.orch = Orchestrator(self.store, self.guards, self.router,
                                     self.notifier)
        else:
            self.orch = Orchestrator(self.store, self.guards, self.router,
                                     self.notifier, registry)
        seed = demo_fixtures.load_seed()
        self.stores = stores or (list(DEFAULT_MERCHANTS) if live
                                 else seed["stores"])
        self.approved_brands = seed["approved_brands"]
        self.prices = {} if live else demo_fixtures.demo_prices(seed)

    def close(self):
        self.store.close()

    def ingest_files(self, files, default_date, default_store):
        sources = []
        saved = []
        for _field, filename, content in files:
            safe = os.path.basename(filename) or "receipt.txt"
            path = os.path.join(self.inbox_dir, safe)
            with open(path, "wb") as handle:
                handle.write(content)
            saved.append(safe)
            file_date, file_store = parse_filename(safe)
            ext = os.path.splitext(safe)[1].lower()
            if ext == ".txt":
                source = content.decode("utf-8", "replace")
            else:
                source = path
            sources.append({"source": source,
                            "date": file_date or default_date,
                            "store": file_store or default_store})
        if not sources:
            return {"error": "no files uploaded"}
        result = self.orch.run(
            trigger="new_receipt", receipt_sources=sources,
            prices=self.prices, stores=self.stores,
            approved_brands=self.approved_brands, build_cart=True)
        return self._payload(result, saved)

    def demo(self):
        """One-click synthetic receipts for the stage demo."""
        result = self.orch.run(
            trigger="new_receipt",
            receipt_sources=demo_fixtures.sample_receipts(),
            prices=self.prices, stores=self.stores,
            approved_brands=self.approved_brands, build_cart=True)
        return self._payload(result, ["demo-week1", "demo-week2"])

    def _payload(self, result, files):
        return {"files": files,
                "purchases": len(result["purchases"]),
                "review": len(result["review"]),
                "rows": self._cart_rows(result),
                "notes": result["notes"],
                "totals": self._totals(result),
                "approval_id": result["approval_id"],
                "unverified": len(result["unverified"])}

    def _totals(self, result):
        plan = result["plan"]
        if plan is None:
            return {"total": 0.0, "saving": 0.0}
        return {"total": plan.expected_total,
                "saving": plan.saving_vs_last_basket}

    def _cart_rows(self, result):
        plan = result["plan"]
        if plan is None:
            return []
        rows = []
        for item in plan.items:
            product = self.store.get_product(item.product_id)
            name = product.canonical_name if product else item.product_id
            paid = None
            purchases = self.store.purchases_for_product(item.product_id)
            if purchases:
                last = purchases[-1]
                if last.quantity > 0:
                    paid = round(last.price_paid / last.quantity, 2)
            saving = round(paid - item.expected_price, 2) \
                if paid is not None else None
            rows.append({"product_id": item.product_id, "name": name,
                         "paid": paid, "deal": item.expected_price,
                         "store": item.store, "decision": item.decision,
                         "saving": saving})
        return rows

    def approve(self, approval_id, approved):
        status = self.guards.approvals.decide(approval_id, bool(approved))
        plan_id = None
        for entry in self.guards.audit_log:
            if entry["event"] == "cart_pending_approval" \
                    and entry["detail"] == approval_id:
                plan_id = entry["run_id"]
        return {"approval_id": approval_id, "status": status,
                "run_id": plan_id}


class Handler(BaseHTTPRequestHandler):
    server_version = "SmartPantry/0.1"

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type):
        with open(path, "rb") as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_file(os.path.join(STATIC_DIR, "index.html"),
                            "text/html; charset=utf-8")
        elif parsed.path == "/api/health":
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            if parsed.path == "/api/ingest":
                content_type = self.headers.get("Content-Type", "")
                boundary = ""
                for part in content_type.split(";"):
                    if "boundary=" in part:
                        boundary = part.split("boundary=", 1)[1].strip()
                if not boundary:
                    self._send_json({"error": "multipart upload required"},
                                    status=400)
                    return
                query = parse_qs(parsed.query)
                date = query.get("date", ["2026-11-03"])[0]
                store = query.get("store", ["Mariano's"])[0]
                files = parse_multipart(body, boundary.encode("utf-8"))
                self._send_json(self.app.ingest_files(files, date, store))
            elif parsed.path == "/api/demo":
                self._send_json(self.app.demo())
            elif parsed.path == "/api/approve":
                payload = json.loads(body.decode("utf-8") or "{}")
                try:
                    self._send_json(self.app.approve(
                        payload.get("approval_id", ""),
                        payload.get("approved", False)))
                except KeyError:
                    self._send_json({"error": "unknown approval"},
                                    status=404)
            else:
                self._send_json({"error": "not found"}, status=404)
        except Exception as exc:  # demo server: report, never hang
            self._send_json({"error": str(exc)}, status=500)


def create_server(port=8000, db_path="smartpantry-web.db",
                  inbox_dir="receipts", live=False, replay_only=False,
                  stores=None):
    server = HTTPServer(("127.0.0.1", port),
                        Handler)
    server.app = App(db_path, inbox_dir, live=live,
                     replay_only=replay_only, stores=stores)
    return server
