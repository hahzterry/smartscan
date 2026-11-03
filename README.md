# SmartScan · SmartPantry

A personal grocery agent that learns what you actually buy from your receipts,
re-checks those prices across your stores on a schedule, and tells you where
and when to buy — acting only inside guardrails you set.

The Python package is **`smartpantry`**; this repository is **SmartScan**.

It implements the A.G.E.N.T plan (see `Grocery Price Agent — A.G.E.N.T Plan_v2.pdf`):

| Stage | Agent | What it does |
|---|---|---|
| **A**cquire | `agents/acquire.py` | Parses receipts / e-receipts into line items, redacts PII |
| **G**ather | `agents/gather.py` | Fetches current prices: API → flyer → browser fallback |
| **E**valuate | `agents/evaluate.py` | Normalises unit prices, matches products, checks sanity bands |
| **N**avigate | `agents/navigate.py` | Builds the weekly plan: buy now / switch store / wait, stop analysis |
| **T**ake action | `agents/take_action.py` | Notifies and drafts a cart — held behind an approval gate |

Gather ⇄ Evaluate run as a retry loop, all wrapped in a guardrail layer.

```text
receipts ──► Acquire ──► Gather ⇄ Evaluate ──► Navigate ──► Take action ──► (you approve)
                │            └── re-gather (max 3/item) ──┘                     │
                └──────────────────── SQLite store · traces · audit log ◄────────┘
```

**Stdlib-only Python 3.9+** — no third-party installs. Stores and prices in the
fixtures are fictional.

## Quickstart

```sh
python3 -m venv .venv && source .venv/bin/activate   # or: uv venv
python -m unittest discover -s tests   # 124 tests
python -m smartpantry demo             # end-to-end run on fixtures
python -m smartpantry eval             # 50-receipt no-touch eval (gate: 0.90)
```

The demo ingests 22 synthetic purchases, prices the watchlist across 3 stores,
prints a weekly plan with savings vs your last basket, and drafts a cart that
waits for approval — nothing is ordered.

## Commands

| Command | Purpose |
|---|---|
| `init-db --db smartpantry.db` | Create and seed the SQLite database |
| `ingest receipt.txt --date 2026-11-02 --store Northmart` | Ingest one receipt |
| `ingest --inbox receipts/ [--keep]` | Batch-scan a folder of receipts |
| `sweep [--cart] [--stores …]` | Re-price the watchlist and build the plan (`--cart` drafts a cart) |
| `sweep --live [--replay-only]` | Price from Flipp weekly deals (replay = cached, offline) |
| `eval [--eval-set eval/eval_receipts.json]` | Run the receipt-parsing eval |
| `demo` | End-to-end run on fixtures |
| `serve [--port 8000] [--live --replay-only]` | Start the local web app |

All run as `python -m smartpantry <command>`; add `--help` to any for flags.
Store names containing an apostrophe need quoting: `--store "Mariano's"`.

## Web app

```sh
python -m smartpantry serve --port 8000
```

Open http://127.0.0.1:8000/ — a single dependency-free page that works on phone
and desktop:

1. **Receipts** — upload files (or use the phone camera button), or *Load demo receipts*.
2. **SmartCart** — per-item recommendation (`buy now` / `switch store` / `wait`),
   ad-price labels with end dates, loyalty/coupon conditions, stop analysis.
3. **Draft cart** — *Approve* or *Decline*; nothing is ordered until you approve.

API routes: `GET /api/health`, `POST /api/ingest`, `POST /api/demo`,
`POST /api/approve`. Full walkthrough in [`docs/UI_FLOW.md`](docs/UI_FLOW.md).

Items join the recommendation only after 2+ purchases.

## Receipt input

Text format, one item per line:

```text
2 x Organic Bananas (12 oz) @ $1.29
```

JSON fixtures with the same fields also load. `.txt` / `.json` parse offline;
photos and PDFs (`.jpg` / `.png` / `.pdf`) need the vision backend
(`META_MODEL_API_*`) or a text transcription first.

**Inbox naming:** `YYYY-MM-DD_Store_rest.ext` (e.g. `2026-11-01_Marianos_eggs.jpg`).
`--date` / `--store` fill in whatever the filename doesn't say. Scanned files
move to `receipts/processed/` unless `--keep`.

## Live prices (Flipp weekly deals)

```sh
export FLIPP_POSTAL_CODE=60601
export FLIPP_MERCHANTS="Mariano's,Target,Walmart"   # optional filter
python -m smartpantry sweep --live
python -m smartpantry sweep --live --replay-only     # offline, from cache
```

Flipp needs no key: deals are fetched once per postal code per week and cached
to `flipp_cache.json`. It is an undocumented source, so it fails soft —
unreachable means misses and the run says deals were unavailable. Deals past
their valid-to date are dropped; conditional prices (loyalty card, coupon,
multi-buy) are labelled.

The Walmart affiliate and Target RedSky connectors
(`tools/live_connectors.py`) remain as optional sources; `--live` currently
resolves everything through Flipp.

## Configuration

**Environment variables**

| Variable | Used for |
|---|---|
| `META_MODEL_API_BASE`, `META_MODEL_API_KEY` | Live model calls (OpenAI-compatible). Unset → deterministic fake backend |
| `FLIPP_POSTAL_CODE`, `FLIPP_MERCHANTS`, `FLIPP_API_BASE` | Flipp live deals |
| `WALMART_API_BASE`, `WALMART_CONSUMER_ID`, `WALMART_KEY_VERSION`, `WALMART_PRIVATE_KEY` / `WALMART_PRIVATE_KEY_PATH` | Optional Walmart connector |
| `TARGET_REDSKY_BASE`, `TARGET_REDSKY_KEY`, `TARGET_STORE_ID` | Optional Target connector |

**Guardrail starting values** (tune in `smartpantry/config.py`)

| Guardrail | Default |
|---|---|
| Extraction confidence threshold | 0.70 |
| Sanity band (beyond needs a second source) | ±40% |
| Price freshness TTL | flyer 7d · API 1d · browser 1d |
| Loop caps | 3 re-gathers per item · 50 steps per run |
| Weekly spend cap | $250 |
| Token budget | 200k per run |
| Approval required | `build_cart`, `submit_order`, `spend` |

Each stage may call only its own tools (`TOOL_ALLOWLIST`); Navigate has no
fetch or purchase tools by design.

## Model routing and the data rule

`model_router.py` is one OpenAI-compatible layer over three tiers:

| Tier | Model | Default stages |
|---|---|---|
| standard | Muse Spark 1.3 (hosted) | Acquire, Gather, Navigate, Take action |
| open | self-hosted open model | Evaluate |
| contributor | low-cost tier | **synthetic/test data only** |

Real receipts go through the standard tier or the self-hosted open model —
the router raises if real data is ever routed to Contributor. Tests and the
demo use the deterministic fake backend.

## Project layout

```text
smartpantry/
  agents/          one stage agent per letter (prompt, tool allowlist, output contract)
  tools/           receipt parser, e-receipt reader, store connectors, Flipp
                   connector + cache, unit-price normaliser, product matcher, notifier
  orchestrator.py  run state machine with the Gather ⇄ Evaluate retry loop
  guardrails.py    schema/confidence checks, sanity bands, TTLs, caps, allowlists,
                   approval gate, spend/brand limits, token budgets, PII redaction, audit log
  model_router.py  tiered model routing + data rule
  schemas.py       typed contracts between stages
  store.py         SQLite shared store
  inbox.py         folder-drop batch scanning
  scheduler.py     scheduled sweeps
  observability.py traces + eval harness
  web.py, static/  local web app
  data/seed_products.json   30-product demo catalog
eval/              50 labelled synthetic receipts + generator (seed 42)
tests/             unittest suite
docs/UI_FLOW.md    web app flow
```

## Open decisions (from the plan)

- Which 3 stores and region?
- Self-host Spark Open for Evaluate in v1, or run everything hosted?
- Personal-only or multi-household-ready?
- Alerts + plan only, or the approval-gated cart?
