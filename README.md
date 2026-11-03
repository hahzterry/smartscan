# SmartPantry

A personal agent that learns what you actually buy, re-checks those prices
across your stores on a schedule, and tells you where and when to buy —
acting only inside guardrails you set. Implements the A.G.E.N.T plan
(`Grocery Price Agent — A.G.E.N.T Plan.pdf`): Acquire · Gather · Evaluate ·
Navigate · Take action, wrapped in a guardrail layer and closed into a loop.

Stdlib-only Python (3.9+): no third-party installs needed. Stores and prices
in the fixtures are fictional.

## Quickstart

```sh
uv venv
source .venv/bin/activate
python -m unittest discover -s tests   # focused suite, ~70 checks
python -m smartpantry demo           # end-to-end run on fixtures
python -m smartpantry eval           # 50-receipt no-touch eval (gate: 0.90)
```

Without `uv`, `python3 -m venv .venv` then the same commands work.

## Commands

```sh
python -m smartpantry init-db --db smartpantry.db
python -m smartpantry ingest receipt.txt --date 2026-11-02 --store Northmart
python -m smartpantry ingest --inbox receipts/      # scan a folder of scans
python -m smartpantry sweep --cart     # draft cart waits for approval
python -m smartpantry sweep --live --replay-only   # Flipp deals, offline replay
python -m smartpantry eval --eval-set eval/eval_receipts.json
python -m smartpantry demo
python -m smartpantry serve                         # local web app
```

## Web app

```sh
python -m smartpantry serve --port 8000
```

Open http://127.0.0.1:8000/ in a browser: upload receipt files (or snap
one with the phone camera button), see the SmartCart breakdown
with ad-price labels, and approve or decline the draft cart. Add
`--live --replay-only` to price from the Flipp cache instead of
fixtures. The page is one dependency-free file good on phone and
desktop.

Receipt text format (one item per line):

```text
2 x Organic Bananas (12 oz) @ $1.29
```

JSON fixtures with the same fields also load. Receipt photos/PDFs go to the
model backend for vision (Muse Spark 1.3 vision + strict JSON).

## Inbox (folder drop)

Drop scans into a folder and ingest them in one batch:

```sh
mkdir -p receipts
# name files YYYY-MM-DD_Store_rest.ext, e.g. 2026-11-01_Marianos_eggs.jpg
python -m smartpantry ingest --inbox receipts/ --date 2026-11-02 --store Mariano's
```

`.txt` and `.json` parse offline; `.jpg` / `.png` / `.pdf` need the vision
backend (`META_MODEL_API_*`) or a transcription to text first. Scanned files
move to `receipts/processed/` unless `--keep`. `--date` / `--store` fill in
whatever the filename doesn't say.

## Layout

- `smartpantry/agents/` — one stage agent per letter (prompt, tool
  allowlist, output contract)
- `smartpantry/tools/` — receipt parser, e-receipt reader, store
  connectors (API → flyer → browser fallback), Flipp weekly-deals
  connector with disk cache, unit-price normaliser, product matcher,
  notifier
- `smartpantry/inbox.py` — folder-drop batch scanning
- `smartpantry/guardrails.py` — schema + confidence checks, sanity bands,
  freshness TTLs, loop/step caps, tool allowlists, approval gate, spend and
  brand limits, token budgets, PII redaction, audit log
- `smartpantry/model_router.py` — OpenAI-compatible routing over
  standard / contributor / open tiers
- `smartpantry/orchestrator.py` — the run state machine with the
  Gather ⇄ Evaluate retry loop
- `smartpantry/store.py` — SQLite shared store; `scheduler.py`,
  `observability.py` (traces + eval harness)
- `smartpantry/data/seed_products.json` — 30-product demo catalog
- `eval/` — 50 labelled synthetic receipts + generator (seed 42)
- `tests/` — focused unittest suite

## Guardrail starting values

Confidence 0.70 · sanity band ±40% (needs a second source) · flyer TTL 7d,
API/browser 1d · max 3 re-gathers per item, 50 steps per run · weekly spend
cap $250 · token budget 200k per run. Tune in `smartpantry/config.py`.

## Live prices (Flipp weekly deals)

```sh
export FLIPP_POSTAL_CODE=60601
export FLIPP_MERCHANTS="Mariano's,Target,Walmart"   # optional filter
python -m smartpantry sweep --live
python -m smartpantry sweep --live --stores "Mariano's"
```

Flipp needs no key: deals are fetched once per postal code per week and
cached to `flipp_cache.json`. Rehearsals and the on-stage demo replay
from cache without touching the network:

```sh
python -m smartpantry sweep --live --replay-only
```

Flipp is an undocumented source, so it fails soft — unreachable means
misses, and the run says deals were unavailable. Deals carry their ad
validity window; anything past valid-to is dropped, and conditional
prices (loyalty card, coupon, multi-buy) are labelled in the plan.

The Walmart affiliate and Target RedSky connectors
(`smartpantry/tools/live_connectors.py`) remain in the tree as optional,
post-hackathon sources; `--live` currently resolves everything through
Flipp.

## Data rule

The Contributor tier only ever sees synthetic/test data. Real receipts go
through the standard tier or the self-hosted open model — the router raises
if this is violated. Set `META_MODEL_API_BASE` / `META_MODEL_API_KEY` for
live model calls; tests and the demo use the deterministic fake backend.

## Open decisions (from the plan)

Which 3 stores and region? Self-host Spark Open for Evaluate in v1 or run
everything hosted? Personal-only or multi-household-ready? Alerts + plan
only, or the approval-gated cart?
