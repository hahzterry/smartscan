
"""CAMP CREEK MARKET web server.

Public API:
- GET / — CAMP CREEK MARKET homepage.
- GET /api/health — Railway health check.
- GET /api/deals — Local advertised deals.
- GET /api/merchants — Configured local merchants.

Protected API:
- POST /api/ingest — Upload receipts.
- POST /api/demo — Load demo receipts.
- POST /api/approve — Approve or decline a draft cart.
"""

import hmac
import json
import math
import os
import traceback

from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from smartpantry import demo as demo_fixtures
from smartpantry.guardrails import GuardrailService
from smartpantry.inbox import parse_filename
from smartpantry.model_router import FakeBackend, ModelRouter
from smartpantry.orchestrator import Orchestrator
from smartpantry.store import Store
from smartpantry.local_deals import LocalDealsService

from smartpantry.tools.flipp import (
    configured_merchants,
    flipp_registry,
)

from smartpantry.tools.notifier import Notifier


STATIC_DIR = os.path.join(
    os.path.dirname(__file__),
    "static",
)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def current_date():
    return datetime.now(
        ZoneInfo("America/New_York")
    ).date().isoformat()


def parse_multipart(body: bytes, boundary: bytes):
    """Extract uploaded receipt files."""

    files = []

    for chunk in body.split(b"--" + boundary):

        if chunk in (
            b"",
            b"--",
            b"--\r\n",
            b"--\n",
        ):
            continue

        head, sep, content = chunk.partition(
            b"\r\n\r\n"
        )

        if not sep:
            head, sep, content = chunk.partition(
                b"\n\n"
            )

        if not sep:
            continue

        if content.endswith(b"\r\n"):
            content = content[:-2]

        elif content.endswith(b"\n"):
            content = content[:-1]

        field = ""
        filename = ""

        for line in head.decode(
            "utf-8", "replace"
        ).splitlines():

            if "content-disposition" not in line.lower():
                continue

            for part in line.split(";"):

                part = part.strip()

                if part.startswith("name="):
                    field = part[5:].strip('"')

                elif part.startswith("filename="):

                    filename = os.path.basename(
                        part[9:].strip('"')
                    )

        if filename:
            files.append(
                (field, filename, content)
            )

    return files


class App:

    def __init__(
        self,
        db_path,
        inbox_dir,
        live=False,
        replay_only=False,
        stores=None,
    ):

        self.inbox_dir = inbox_dir
        self.live = live
        self.replay_only = replay_only

        os.makedirs(
            inbox_dir,
            exist_ok=True,
        )

        db_directory = os.path.dirname(
            db_path
        )

        if db_directory:

            os.makedirs(
                db_directory,
                exist_ok=True,
            )

        # Receipt database.

        self.store = Store(db_path)

        if not self.store.list_products():

            demo_fixtures.seed_store(
                self.store
            )

        # Existing SmartPantry services.

        self.guards = GuardrailService()

        self.router = ModelRouter({
            "standard": FakeBackend(),
            "contributor": FakeBackend(),
            "open": FakeBackend(),
        })

        self.notifier = Notifier(
            backend="memory"
        )

        # Camp Creek merchant configuration.

        self.local_merchants = (
            configured_merchants()
        )

        seed = demo_fixtures.load_seed()

        self.stores = stores or (
            self.local_merchants
            if live
            else seed["stores"]
        )

        self.approved_brands = (
            seed["approved_brands"]
        )

        self.prices = (
            {}
            if live
            else demo_fixtures.demo_prices(seed)
        )

        # Existing receipt price connector.

        registry = (
            flipp_registry(
                merchants=self.local_merchants,
                replay_only=replay_only,
            )
            if live
            else None
        )

        if registry is None:

            self.orch = Orchestrator(
                self.store,
                self.guards,
                self.router,
                self.notifier,
            )

        else:

            self.orch = Orchestrator(
                self.store,
                self.guards,
                self.router,
                self.notifier,
                registry,
            )

        # Independent public local deals service.
        # No receipts are required.

        self.local_deals = LocalDealsService(
            replay_only=replay_only or not live
        )

        self.local_deals.start()

        print(
            "CAMPDEALZ local deals service started",
            flush=True,
        )

        print(
            "Configured merchants: "
            + ", ".join(self.local_merchants),
            flush=True,
        )

    def close(self):

        self.local_deals.close()
        self.store.close()

    def ingest_files(
        self,
        files,
        default_date,
        default_store,
    ):

        sources = []
        saved = []

        for _field, filename, content in files:

            safe = (
                os.path.basename(filename)
                or "receipt.txt"
            )

            if safe in (".", ".."):
                continue

            path = os.path.join(
                self.inbox_dir,
                safe,
            )

            with open(path, "wb") as handle:
                handle.write(content)

            saved.append(safe)

            file_date, file_store = (
                parse_filename(safe)
            )

            ext = os.path.splitext(
                safe
            )[1].lower()

            if ext == ".txt":

                source = content.decode(
                    "utf-8",
                    "replace",
                )

            else:

                source = path

            sources.append({
                "source": source,
                "date": file_date or default_date,
                "store": file_store or default_store,
            })

        if not sources:

            return {
                "error": "No files uploaded"
            }

        result = self.orch.run(
            trigger="new_receipt",
            receipt_sources=sources,
            prices=self.prices,
            stores=self.stores,
            approved_brands=self.approved_brands,
            build_cart=True,
        )

        return self._payload(
            result,
            saved,
        )

    def demo(self):

        """Load synthetic receipt examples."""

        result = self.orch.run(
            trigger="new_receipt",
            receipt_sources=(
                demo_fixtures.sample_receipts()
            ),
            prices=self.prices,
            stores=self.stores,
            approved_brands=self.approved_brands,
            build_cart=True,
        )

        return self._payload(
            result,
            ["demo-week1", "demo-week2"],
        )

    def _payload(self, result, files):

        return {
            "files": files,
            "purchases": len(
                result["purchases"]
            ),
            "review": len(
                result["review"]
            ),
            "rows": self._cart_rows(result),
            "notes": result["notes"],
            "totals": self._totals(result),
            "approval_id": result["approval_id"],
            "unverified": len(
                result["unverified"]
            ),
        }

    def _totals(self, result):

        plan = result["plan"]

        if plan is None:

            return {
                "total": 0.0,
                "saving": 0.0,
            }

        return {
            "total": plan.expected_total,
            "saving": plan.saving_vs_last_basket,
        }

    def _cart_rows(self, result):

        plan = result["plan"]

        if plan is None:
            return []

        rows = []

        for item in plan.items:

            product = self.store.get_product(
                item.product_id
            )

            name = (
                product.canonical_name
                if product
                else item.product_id
            )

            paid = None

            purchases = (
                self.store.purchases_for_product(
                    item.product_id
                )
            )

            if purchases:

                last = purchases[-1]

                if last.quantity > 0:

                    paid = round(
                        last.price_paid
                        / last.quantity,
                        2,
                    )

            saving = (
                round(
                    paid - item.expected_price,
                    2,
                )
                if paid is not None
                else None
            )

            rows.append({
                "product_id": item.product_id,
                "name": name,
                "paid": paid,
                "deal": item.expected_price,
                "store": item.store,
                "decision": item.decision,
                "saving": saving,
            })

        return rows

    def approve(
        self,
        approval_id,
        approved,
    ):

        status = self.guards.approvals.decide(
            approval_id,
            approved,
        )

        plan_id = None

        for entry in self.guards.audit_log:

            if (
                entry["event"]
                == "cart_pending_approval"
                and entry["detail"]
                == approval_id
            ):

                plan_id = entry["run_id"]

        return {
            "approval_id": approval_id,
            "status": status,
            "run_id": plan_id,
        }


class Handler(BaseHTTPRequestHandler):

    server_version = "CAMPDEALZ/0.2"

    @property
    def app(self):

        return self.server.app

    def log_message(self, fmt, *args):

        if os.environ.get(
            "HTTP_LOGGING", "0"
        ) == "1":

            super().log_message(
                fmt,
                *args,
            )

    def _send_json(
        self,
        payload,
        status=200,
    ):

        body = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )

        self.send_header(
            "Content-Length",
            str(len(body)),
        )

        self.send_header(
            "Cache-Control",
            "no-store",
        )

        self.send_header(
            "X-Content-Type-Options",
            "nosniff",
        )

        self.end_headers()
        self.wfile.write(body)

    def _send_file(
        self,
        path,
        content_type,
    ):

        if not os.path.isfile(path):

            self._send_json(
                {"error": "File not found"},
                status=404,
            )

            return

        with open(path, "rb") as handle:

            body = handle.read()

        self.send_response(200)

        self.send_header(
            "Content-Type",
            content_type,
        )

        self.send_header(
            "Content-Length",
            str(len(body)),
        )

        self.send_header(
            "Cache-Control",
            "no-cache",
        )

        self.send_header(
            "X-Content-Type-Options",
            "nosniff",
        )

        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):

        admin_token = os.environ.get(
            "ADMIN_TOKEN", ""
        )

        supplied_token = self.headers.get(
            "Authorization", ""
        )

        if not admin_token:
            return False

        expected = "Bearer " + admin_token

        return hmac.compare_digest(
            supplied_token.encode("utf-8"),
            expected.encode("utf-8"),
        )

    def do_GET(self):

        parsed = urlparse(self.path)

        try:

            # Public CAMPDEALZ homepage.

            if parsed.path == "/":

                self._send_file(
                    os.path.join(
                        STATIC_DIR,
                        "index.html",
                    ),
                    "text/html; charset=utf-8",
                )

            # Railway health check.

            elif parsed.path == "/api/health":

                self._send_json({
                    "ok": True,
                    "app": "CAMPDEALZ",
                    "version": "0.2.0",
                })

            # Configured Camp Creek merchants.

            elif parsed.path == "/api/merchants":

                self._send_json({
                    "location": "Camp Creek Parkway",
                    "city": "Atlanta",
                    "state": "GA",
                    "merchants": (
                        self.app.local_deals.merchants
                    ),
                    "postal_codes": (
                        self.app.local_deals.postal_codes
                    ),
                    "branch_verified": False,
                })

            # Public local deals.
            # No receipt or login required.

            elif parsed.path == "/api/deals":

                query = parse_qs(
                    parsed.query
                )

                merchant = query.get(
                    "merchant", [""]
                )[0].strip()

                search = query.get(
                    "search", [""]
                )[0].strip()

                try:

                    min_discount = float(
                        query.get(
                            "min_discount",
                            ["0"],
                        )[0]
                    )

                    max_price = query.get(
                        "max_price",
                        [""],
                    )[0]

                    max_price = (
                        float(max_price)
                        if max_price
                        else None
                    )

                    if (
                        not math.isfinite(min_discount)
                        or not 0 <= min_discount <= 100
                    ):

                        raise ValueError(
                            "Invalid discount"
                        )

                    if max_price is not None:

                        if (
                            not math.isfinite(max_price)
                            or max_price < 0
                        ):

                            raise ValueError(
                                "Invalid price"
                            )

                except ValueError:

                    self._send_json(
                        {
                            "error": "Invalid deal filter"
                        },
                        status=400,
                    )

                    return

                payload = (
                    self.app.local_deals.query(
                        merchant=merchant,
                        search=search,
                        min_discount=min_discount,
                        max_price=max_price,
                    )
                )

                self._send_json(payload)

            # Ignore missing favicon.

            elif parsed.path == "/favicon.ico":

                self.send_response(204)
                self.end_headers()

            else:

                self._send_json(
                    {"error": "Not found"},
                    status=404,
                )

        except Exception:

            traceback.print_exc()

            self._send_json(
                {"error": "Internal server error"},
                status=500,
            )

    def do_POST(self):

        parsed = urlparse(self.path)

        allowed_paths = (
            "/api/ingest",
            "/api/demo",
            "/api/approve",
        )

        if parsed.path not in allowed_paths:

            self._send_json(
                {"error": "Not found"},
                status=404,
            )

            return

        # Protect private receipt data.

        if not self._authorized():

            self._send_json(
                {"error": "Unauthorized"},
                status=401,
            )

            return

        try:

            length = int(
                self.headers.get(
                    "Content-Length",
                    "0",
                )
            )

        except ValueError:

            self._send_json(
                {"error": "Invalid content length"},
                status=400,
            )

            return

        if length < 0:

            self._send_json(
                {"error": "Invalid content length"},
                status=400,
            )

            return

        if length > MAX_UPLOAD_BYTES:

            self._send_json(
                {
                    "error": "Upload exceeds 20 MB"
                },
                status=413,
            )

            return

        body = (
            self.rfile.read(length)
            if length
            else b""
        )

        try:

            # Upload private receipts.

            if parsed.path == "/api/ingest":

                content_type = self.headers.get(
                    "Content-Type",
                    "",
                )

                boundary = ""

                for part in content_type.split(";"):

                    if "boundary=" in part:

                        boundary = (
                            part.split(
                                "boundary=",
                                1,
                            )[1]
                            .strip()
                            .strip('"')
                        )

                if not boundary:

                    self._send_json(
                        {
                            "error": (
                                "Multipart upload required"
                            )
                        },
                        status=400,
                    )

                    return

                query = parse_qs(
                    parsed.query
                )

                date = query.get(
                    "date",
                    [current_date()],
                )[0]

                default_merchant = (
                    self.app.local_merchants[0]
                    if self.app.local_merchants
                    else "Unknown"
                )

                store = query.get(
                    "store",
                    [default_merchant],
                )[0]

                files = parse_multipart(
                    body,
                    boundary.encode("utf-8"),
                )

                result = self.app.ingest_files(
                    files,
                    date,
                    store,
                )

                self._send_json(
                    result,
                    status=(
                        400
                        if result.get("error")
                        else 200
                    ),
                )

            # Private demo.

            elif parsed.path == "/api/demo":

                self._send_json(
                    self.app.demo()
                )

            # Private cart approval.

            elif parsed.path == "/api/approve":

                try:

                    payload = json.loads(
                        body.decode("utf-8")
                        or "{}"
                    )

                except (ValueError, UnicodeError):

                    self._send_json(
                        {"error": "Invalid JSON"},
                        status=400,
                    )

                    return

                if not isinstance(payload, dict):

                    self._send_json(
                        {"error": "Invalid payload"},
                        status=400,
                    )

                    return

                approval_id = payload.get(
                    "approval_id", ""
                )

                approved = payload.get(
                    "approved"
                )

                if (
                    not isinstance(approval_id, str)
                    or not approval_id
                    or not isinstance(approved, bool)
                ):

                    self._send_json(
                        {
                            "error": (
                                "Approval ID and boolean "
                                "approved value required"
                            )
                        },
                        status=400,
                    )

                    return

                try:

                    self._send_json(
                        self.app.approve(
                            approval_id,
                            approved,
                        )
                    )

                except KeyError:

                    self._send_json(
                        {
                            "error": "Unknown approval"
                        },
                        status=404,
                    )

        except Exception:

            traceback.print_exc()

            self._send_json(
                {
                    "error": "Internal server error"
                },
                status=500,
            )


def create_server(
    port=8000,
    db_path="smartpantry-web.db",
    inbox_dir="receipts",
    live=False,
    replay_only=False,
    stores=None,
):

    # Railway network configuration.

    host = os.environ.get(
        "HOST",
        "0.0.0.0",
    )

    port = int(
        os.environ.get(
            "PORT",
            port,
        )
    )

    # Optional persistent Railway storage.

    db_path = os.environ.get(
        "DB_PATH",
        db_path,
    )

    inbox_dir = os.environ.get(
        "INBOX_DIR",
        inbox_dir,
    )

    server = HTTPServer(
        (host, port),
        Handler,
    )

    try:

        server.app = App(
            db_path=db_path,
            inbox_dir=inbox_dir,
            live=live,
            replay_only=replay_only,
            stores=stores,
        )

    except Exception:

        server.server_close()
        raise

    print(
        "=" * 50,
        flush=True,
    )

    print(
        "CAMPDEALZ SERVER STARTED",
        flush=True,
    )

    print(
        f"Listening on {host}:{port}",
        flush=True,
    )

    print(
        f"Live mode: {live}",
        flush=True,
    )

    print(
        "Location: Camp Creek Parkway",
        flush=True,
    )

    print(
        "Public deals API: /api/deals",
        flush=True,
    )

    print(
        "Health check: /api/health",
        flush=True,
    )

    print(
        "=" * 50,
        flush=True,
    )

    return server
