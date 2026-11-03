# SmartPantry UI Flow

Single-page web app (`smartpantry/static/index.html`, dependency-free) served by
`python -m smartpantry serve` at `http://127.0.0.1:8000/`. One page, three
sections, one decision per section. Works on phone (camera upload) and desktop.

## Flow overview

```text
Open page
  └─► 1 · Receipts ── Scan ──► pipeline runs ──► 2 · SmartCart
                                                      │
                                                      ▼ (draft cart exists)
                                                3 · Draft cart ── Approve / Decline
```

`Load demo receipts` skips uploading: it ingests two built-in synthetic
receipts and jumps straight to section 2.

## Section 1 · Receipts (input)

Elements:

- File picker — `.txt`, `.json`, photos, PDF; multiple files allowed.
- Phone camera button — opens the camera directly (`capture="environment"`).
- Date / Store fields — defaults used only when a filename does not follow
  `YYYY-MM-DD_Store_rest.ext`.
- **Scan receipts** button; **Load demo receipts** button; status line.

Action `Scan receipts`:

1. Uploads files via `POST /api/ingest?date=…&store=…` (multipart).
2. Server saves files to the inbox, extracts line items, matches products,
   re-prices the watchlist, builds the plan and a draft cart.
3. Status line reports `N item(s) read, M queued for review`, then section 2
   renders. If a cart was drafted, section 3 appears.

Action `Load demo receipts`: `POST /api/demo`, same rendering, no files needed.

Error states (red status line, sections stay hidden):

- No files chosen: `Choose at least one receipt file first.`
- Server reachable but failed: `Error: <reason>` (e.g. empty upload).
- Server not running / page opened as a file: `Error: Failed to fetch` —
  start the server and use the `http://` URL.

Notes:

- `.txt` / `.json` parse offline. Photos and PDFs need the vision backend
  (`META_MODEL_API_*`); without it their lines land in review/rejected and
  the counts say so.
- Items join the recommendation only after 2+ purchases. A first-ever upload
  renders the "no priced items yet" hint instead of rows.

## Section 2 · SmartCart (recommendation)

Rendered from the ingest/demo response. Contents:

- Headline saving vs last basket, plus the deal total.
- One row per watched item: name, decision pill, `You paid $X · deal $Y @
  Store`, and the per-item saving.
- Decision pills: `buy now` (cheapest at your usual store), `switch store`
  (cheaper elsewhere), `wait` (overpriced right now — hold off).
- Notes: ad-price labels with end dates (`… is this week's ad price (ends
  …)`), condition flags (`needs loyalty card`), stop analysis
  (`consolidated to …` / `N stops needed` / `… stops worth it`), and
  `no verified price for …` for items without current data.

No cart action lives here — this section is advice only.

## Section 3 · Draft cart (approval gate)

Appears only when the run drafted a cart (`approval_id` present). Shows the
cart total and the rule: nothing is ordered until you approve.

- **Approve cart** → `POST /api/approve {"approval_id","approved":true}` →
  status line shows `Cart approved.`
- **Decline** → same with `false` → `Cart rejected.`
- Unknown/expired approval id → `Error: unknown approval`.

The gate is the same server-side approval object the CLI uses: a cart can
never transition without an explicit decision.

## API contract (for reference)

| Call | Body | Returns |
|---|---|---|
| `GET /api/health` | — | `{"ok": true}` |
| `POST /api/ingest` | multipart files | files, purchases, review, rows, notes, totals, approval_id, unverified |
| `POST /api/demo` | — | same shape, synthetic receipts |
| `POST /api/approve` | approval_id, approved | approval_id, status |

Each row: product_id, name, paid (last unit price), deal, store, decision,
saving. Totals: total, saving.

## Stage demo script (60 seconds)

1. Open the URL. 2. Click **Load demo receipts**. 3. Read the saving
headline and one ad-price note aloud. 4. Click **Approve cart**. 5. Show
`Cart approved.` — done.
