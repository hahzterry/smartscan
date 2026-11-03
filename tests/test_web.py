"""Web layer: multipart parsing, page content, and HTTP round-trips.

Requests run through the real handler over an in-memory socket pair, so
routing, ingestion, and approval are exercised with no network binding.
"""

import io
import json
import os
import tempfile
import unittest

from smartpantry.web import App, Handler, STATIC_DIR, parse_multipart

RECEIPT = ("1 x Northfarm Whole Milk (3.79 L) @ $4.29\n"
           "1 x Harvest Gold Ground Coffee (12 oz) @ $8.99")


def _multipart(files, boundary="BOUNDARY"):
    body = b""
    for filename, content in files:
        body += b"--" + boundary.encode() + b"\r\n"
        body += ('Content-Disposition: form-data; name="receipts"; '
                 'filename="%s"\r\n' % filename).encode()
        body += b"Content-Type: text/plain\r\n\r\n"
        body += content + b"\r\n"
    return body + b"--" + boundary.encode() + b"--\r\n"


class FakeSocket:
    def __init__(self, inbound):
        self._reader = io.BytesIO(inbound)
        self.out = io.BytesIO()

    def makefile(self, mode, *_args):
        return self._reader if "r" in mode else self.out

    def sendall(self, data):
        self.out.write(data)


class FakeServer:
    def __init__(self, app):
        self.app = app


class MultipartTest(unittest.TestCase):
    def test_splits_files(self):
        parts = parse_multipart(
            _multipart([("a.txt", b"hello"), ("b.txt", b"bye")]), b"BOUNDARY")
        self.assertEqual([(f, n, c) for f, n, c in parts],
                         [("receipts", "a.txt", b"hello"),
                          ("receipts", "b.txt", b"bye")])

    def test_skips_junk(self):
        self.assertEqual(parse_multipart(b"not multipart", b"BOUNDARY"), [])


class WebApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = App(self.tmp.name + "/web.db",
                       self.tmp.name + "/inbox")
        self.addCleanup(self.app.close)
        self.server = FakeServer(self.app)

    def _request(self, raw):
        sock = FakeSocket(raw)
        Handler(sock, ("127.0.0.1", 0), self.server)
        head, _, body = sock.out.getvalue().partition(b"\r\n\r\n")
        status = int(head.split(b" ")[1])
        return status, body

    def _get(self, path):
        raw = ("GET %s HTTP/1.0\r\nHost: x\r\n\r\n" % path).encode()
        return self._request(raw)

    def _post(self, path, body, content_type):
        raw = (("POST %s HTTP/1.0\r\nHost: x\r\n"
                "Content-Type: %s\r\nContent-Length: %d\r\n\r\n")
               % (path, content_type, len(body))).encode() + body
        return self._request(raw)

    def _ingest(self, files):
        return self._post(
            "/api/ingest?store=Northmart", _multipart(files),
            "multipart/form-data; boundary=BOUNDARY")

    def test_health(self):
        status, body = self._get("/api/health")
        self.assertEqual((status, json.loads(body)), (200, {"ok": True}))

    def test_index_serves_page(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        html = body.decode()
        self.assertIn("SmartPantry", html)
        self.assertIn("/api/ingest", html)

    def test_unknown_route_404s(self):
        status, _body = self._get("/nope")
        self.assertEqual(status, 404)

    def test_ingest_returns_cart_rows_and_approval(self):
        status, body = self._ingest(
            [("2026-10-20_Northmart_a.txt", RECEIPT.encode())])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["purchases"], 2)
        status, body = self._ingest(
            [("2026-10-27_Northmart_b.txt", RECEIPT.encode())])
        data = json.loads(body)
        self.assertEqual(len(data["rows"]), 2)
        names = {row["name"] for row in data["rows"]}
        self.assertEqual(names, {"Whole Milk", "Ground Coffee"})
        self.assertIsNotNone(data["approval_id"])
        milk = [r for r in data["rows"] if r["name"] == "Whole Milk"][0]
        self.assertEqual(milk["paid"], 4.29)
        self.assertIsNotNone(milk["deal"])

    def test_demo_loads_synthetic(self):
        status, body = self._post("/api/demo", b"", "application/json")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["purchases"], 22)
        self.assertTrue(len(data["rows"]) >= 10)
        self.assertIsNotNone(data["approval_id"])

    def test_approve_gate(self):
        self._ingest([("2026-10-20_Northmart_a.txt", RECEIPT.encode())])
        _status, body = self._ingest(
            [("2026-10-27_Northmart_b.txt", RECEIPT.encode())])
        approval_id = json.loads(body)["approval_id"]
        self.assertIsNotNone(approval_id)
        status, body = self._post(
            "/api/approve",
            json.dumps({"approval_id": approval_id,
                        "approved": True}).encode(),
            "application/json")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "approved")
        status, _body = self._post(
            "/api/approve",
            json.dumps({"approval_id": "approval-999",
                        "approved": True}).encode(),
            "application/json")
        self.assertEqual(status, 404)

    def test_empty_upload_rejected(self):
        status, body = self._post(
            "/api/ingest", b"--BOUNDARY--\r\n",
            "multipart/form-data; boundary=BOUNDARY")
        self.assertEqual(status, 200)
        self.assertIn("error", json.loads(body))

    def test_page_static_content(self):
        with open(os.path.join(STATIC_DIR, "index.html"),
                  encoding="utf-8") as handle:
            html = handle.read()
        for marker in ("SmartPantry", "/api/ingest", "/api/approve",
                       'capture="environment"', "prefers-reduced-motion"):
            self.assertIn(marker, html)


if __name__ == "__main__":
    unittest.main()
