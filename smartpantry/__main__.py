"""Command line: init-db, ingest, sweep, plan, eval, demo."""

import argparse
import os
import sys
import tempfile

LIVE_MISS_HINT = "Flipp misses: set FLIPP_POSTAL_CODE (see README, Live prices)"

from . import demo as demo_fixtures
from .guardrails import GuardrailService
from .inbox import archive, scan_inbox
from .model_router import FakeBackend, ModelRouter
from .observability import run_eval
from .orchestrator import Orchestrator
from .store import Store
from .tools.flipp import DEFAULT_MERCHANTS as FLIPP_DEFAULT_MERCHANTS
from .tools.flipp import flipp_registry
from .tools.notifier import Notifier
from .tools.receipt_parser import parse_receipt_text
from .web import create_server


def _services(db_path: str, live: bool = False, replay_only: bool = False):
    store = Store(db_path)
    guards = GuardrailService()
    router = ModelRouter({"standard": FakeBackend(),
                          "contributor": FakeBackend(), "open": FakeBackend()})
    notifier = Notifier()
    if not live:
        return store, Orchestrator(store, guards, router, notifier)
    return store, Orchestrator(store, guards, router, notifier,
                               flipp_registry(replay_only=replay_only))


def cmd_init_db(args: argparse.Namespace) -> int:
    store = Store(args.db)
    seed = demo_fixtures.seed_store(store)
    store.close()
    print("seeded %d products into %s" % (len(seed["products"]), args.db))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    store, orch = _services(args.db, live=args.live,
                            replay_only=args.replay_only)
    inbox_entries: list = []
    skipped: list = []
    if args.inbox:
        scanned = scan_inbox(args.inbox, args.date, args.store)
        inbox_entries = scanned["entries"]
        skipped = scanned["skipped"]
        if not inbox_entries:
            print("inbox %s holds no supported receipts" % args.inbox)
            store.close()
            return 1
        receipt_sources = [{"source": e["source"], "date": e["date"],
                            "store": e["store"]} for e in inbox_entries]
    elif args.receipt:
        with open(args.receipt, "r", encoding="utf-8") as handle:
            text = handle.read()
        receipt_sources = [{"source": text, "date": args.date,
                            "store": args.store}]
    else:
        print("give a receipt file or --inbox DIR")
        store.close()
        return 1
    stores = args.stores or (FLIPP_DEFAULT_MERCHANTS if args.live else None)
    result = orch.run(
        trigger="new_receipt",
        receipt_sources=receipt_sources,
        prices={} if args.live else demo_fixtures.demo_prices(),
        stores=stores or demo_fixtures.load_seed()["stores"],
        approved_brands=demo_fixtures.load_seed()["approved_brands"],
        build_cart=args.cart)
    store.close()
    print("ingested %d purchases, %d queued for review" % (
        len(result["purchases"]), len(result["review"])))
    if skipped:
        print("skipped %d unsupported files" % len(skipped))
    if inbox_entries and not args.keep:
        moved = archive(inbox_entries, args.inbox)
        print("archived %d files to processed/" % len(moved))
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    store, orch = _services(args.db, live=args.live,
                            replay_only=args.replay_only)
    seed = demo_fixtures.load_seed()
    stores = args.stores or (FLIPP_DEFAULT_MERCHANTS if args.live
                             else seed["stores"])
    result = orch.run(
        trigger="scheduled",
        prices={} if args.live else demo_fixtures.demo_prices(seed),
        stores=stores,
        approved_brands=seed["approved_brands"], build_cart=args.cart)
    plan = result["plan"]
    store.close()
    if plan is None:
        print("nothing to price yet: add a receipt first")
        return 0
    print("verified %d prices, %d unverified, saving $%.2f" % (
        len(result["verified"]), len(result["unverified"]),
        plan.saving_vs_last_basket))
    if (args.live and result["unverified"]
            and not os.environ.get("FLIPP_POSTAL_CODE")):
        print(LIVE_MISS_HINT)
    if result["approval_id"]:
        print("cart waiting on %s — approve explicitly to proceed"
              % result["approval_id"])
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    report = run_eval(args.eval_set, parse_receipt_text)
    print("receipts: %(receipts)d lines: %(total_lines)d matched: "
          "%(matched)d no-touch rate: %(no_touch_rate).3f" % report)
    for failure in report["failures"][:10]:
        print("FAIL %s want=%s errors=%s" % (
            failure["receipt"], failure["want"], failure["errors"]))
    return 0 if report["no_touch_rate"] >= 0.9 else 1


def cmd_demo(_args: argparse.Namespace) -> int:
    handle, db_path = tempfile.mkstemp(prefix="smartpantry-demo-", suffix=".db")
    os.close(handle)
    try:
        store, orch = _services(db_path)
        seed = demo_fixtures.seed_store(store)
        result = orch.run(
            trigger="new_receipt",
            receipt_sources=demo_fixtures.sample_receipts(),
            prices=demo_fixtures.demo_prices(seed), stores=seed["stores"],
            approved_brands=seed["approved_brands"], build_cart=True,
            run_id="demo")
        plan = result["plan"]
        print("demo: %d purchases -> watchlist priced across %d stores" % (
            len(result["purchases"]), len(seed["stores"])))
        print("demo: total $%.2f, saving $%.2f, cart %s" % (
            plan.expected_total, plan.saving_vs_last_basket,
            result["approval_id"]))
        print("demo: approval gate holds the cart until you say yes; "
              "nothing was ordered.")
        store.close()
    finally:
        os.unlink(db_path)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    server = create_server(port=args.port, db_path=args.db,
                           inbox_dir=args.inbox, live=args.live,
                           replay_only=args.replay_only, stores=args.stores)
    print("SmartPantry at http://127.0.0.1:%d/ (Ctrl-C to stop)"
          % server.server_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.app.close()
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smartpantry")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-db", help="create and seed the database")
    init.add_argument("--db", default="smartpantry.db")
    init.set_defaults(func=cmd_init_db)

    ingest = sub.add_parser("ingest", help="ingest receipts")
    ingest.add_argument("receipt", nargs="?",
                        help="one receipt file (or use --inbox)")
    ingest.add_argument("--inbox",
                        help="scan a folder of receipts/invoices")
    ingest.add_argument("--keep", action="store_true",
                        help="leave inbox files in place")
    ingest.add_argument("--db", default="smartpantry.db")
    ingest.add_argument("--date", default="2026-11-02")
    ingest.add_argument("--store", default="Northmart")
    ingest.add_argument("--stores", nargs="*", default=None)
    ingest.add_argument("--cart", action="store_true")
    ingest.add_argument("--live", action="store_true",
                        help="price via Flipp weekly deals")
    ingest.add_argument("--replay-only", action="store_true",
                        help="replay Flipp cache, no network")
    ingest.set_defaults(func=cmd_ingest)

    sweep = sub.add_parser("sweep", help="re-price watchlist and plan")
    sweep.add_argument("--db", default="smartpantry.db")
    sweep.add_argument("--stores", nargs="*", default=None)
    sweep.add_argument("--cart", action="store_true")
    sweep.add_argument("--live", action="store_true",
                       help="price via Flipp weekly deals")
    sweep.add_argument("--replay-only", action="store_true",
                       help="replay Flipp cache, no network")
    sweep.set_defaults(func=cmd_sweep)

    evaluation = sub.add_parser("eval", help="run the receipt eval set")
    evaluation.add_argument("--eval-set", default="eval/eval_receipts.json")
    evaluation.set_defaults(func=cmd_eval)

    demo = sub.add_parser("demo", help="end-to-end run on fixtures")
    demo.set_defaults(func=cmd_demo)

    serve = sub.add_parser("serve", help="start the local web app")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--db", default="smartpantry-web.db")
    serve.add_argument("--inbox", default="receipts")
    serve.add_argument("--stores", nargs="*", default=None)
    serve.add_argument("--live", action="store_true",
                       help="price via Flipp weekly deals")
    serve.add_argument("--replay-only", action="store_true",
                       help="replay Flipp cache, no network")
    serve.set_defaults(func=cmd_serve)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
