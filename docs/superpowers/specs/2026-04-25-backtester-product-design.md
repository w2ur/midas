# Midas Backtester — Design Spec

**Date:** 2026-04-25
**Status:** Approved (brainstorming → ready for implementation plan)

## Context and goal

The Midas project has accumulated 11 deterministic trading strategies, 10 universes, an OHLCV store, and a working `bt`-based backtest engine. The strategies and engine have so far only been used internally — there is no public surface for visitors to actually run a backtest. The site (`midas.revah.paris`) currently exposes the AI agent competition, but the operator's most personally-valued output during the project's early phase was the backtests themselves.

This spec defines a **public, interactive backtester** as a separate product on the same site. Visitors compose a strategy, pick a universe, set dates and capital, and get a real backtest with equity curve, metrics, and benchmark comparisons. The product is independent of the agent narrative but eventually converges with it: "what if you'd mirrored agent X every day" becomes one of the strategies the backtester can run.

## Non-goals (v1)

- User accounts / saved configs in a database
- Custom ticker baskets (only the existing universes)
- User-authored selectors or managers (no in-browser code execution)
- Intraday data (end-of-day only)
- Strategy "trending" or "popular today" feed
- Benchmarks beyond MSCI World and the coin-flip baseline
- Mobile-first design (responsive yes, mobile-optimized no)

## Architecture

```
site/  ─── /simulate (Astro + minimal client JS)
   │       form state ↔ querystring
   │
   ├─ static cache hit ─→ data/simulations/cache/<config-hash>.json   (built daily)
   │
   └─ cache miss ─→ POST {backtester URL}/run  ─→  Cloud Run service (backtester/)
                                                       │
                                                       └─ imports engine.{selectors,
                                                          managers, universes, baselines}
                                                          and reads data/market/ohlcv/

scripts/fetch_disclosures.py ─→ data/disclosures/{pelosi,insider,13f-*}.jsonl
   (GitHub Actions: weekly STOCK Act + Form 4, quarterly 13F)
```

### Components

1. **`backtester/`** — new sibling directory to `engine/`. FastAPI app deployed to Google Cloud Run. Imports from `engine/` directly via local Python paths; not packaged as a separate library.
2. **`scripts/fetch_disclosures.py`** — new ingestion script. Writes JSONL files matching the existing `data/market/ohlcv/` pattern.
3. **`data/disclosures/`** — new committed data directory: `pelosi.jsonl`, `insider.jsonl`, `13f-berkshire.jsonl`, `13f-ark.jsonl`, `13f-scion.jsonl`.
4. **`data/simulations/cache/`** — committed JSON cache of pre-computed popular configs, keyed by SHA-256 of canonical config.
5. **`site/src/pages/simulate/`** — new Astro section: `index.astro` (builder), interactive form + chart components.
6. **`.github/workflows/fetch-disclosures.yml`** — new workflow, runs the ingestion script on its own cadence.

### Why Google Cloud Run

- Cold start ~2-5s for a Python container (vs ~30-60s on Render's free tier).
- Free tier (2M requests, 360k vCPU-seconds, 180k GB-seconds per month) covers all realistic load.
- Scales to zero, supports long requests up to 60 min, custom domain + automatic HTTPS.
- Deploy via `gcloud run deploy` from a Dockerfile.

The cache-first pattern (described below) means most user interactions never hit the backend; Cloud Run only handles custom parameter combinations.

## Strategy shapes

The builder supports three distinct strategy shapes, each with its own form layout. All three return the same equity-curve schema, so charting and metrics code is shared.

### Signal-driven

Form: `universe` + `selector` + `manager` + `funding` + `rules`.
Maps directly to the existing JSON spec format in `data/strategies/`.
All currently-implemented selectors (golden cross, RSI contrarian, buy-the-dip variants, dogs-of-the-dow, fear-greed, dividend aristocrats, etc.) and managers exposed.
Every parameter that exists in the JSON specs is exposed as a form input.

### Mirror-portfolio

Form: `source` + `rebalance_cadence` + `funding`.
Sources:
- `pelosi` — read from `data/disclosures/pelosi.jsonl`. Mirrors Nancy Pelosi's individual trades (buy = enter, sell = exit).
- `insider-aggregate` — read from `data/disclosures/insider.jsonl`. Aggregation rule: at each rebalance, hold the top N tickers (default 10) ranked by net insider buying in the trailing 90 days, equal-weighted. The Form 4 stream is too noisy to mirror trade-by-trade, so we summarize.
- `13f-berkshire` — read from `data/disclosures/13f-berkshire.jsonl`
- `13f-ark` — read from `data/disclosures/13f-ark.jsonl`
- `13f-scion` — read from `data/disclosures/13f-scion.jsonl`
- `agent-<id>` — read from `data/portfolios/<agent_id>/snapshots.jsonl` (one entry per Midas agent)

Rebalance cadences: `daily`, `weekly`, `monthly`, `quarterly`, `on-source-change` (rebalance only when the underlying source publishes a new disclosure).

**Relationship to existing `pelosi-tracker.json` / `insider-shadow.json` signal-shape strategies:** those files are reactive signal-driven strategies (use a disclosure as an entry signal, manage with the standard manager pipeline). The new mirror-shape sources are stricter: rebalance the entire portfolio to match the source's holdings. Both coexist — different compositions, both useful — and the existing JSON specs remain unchanged.

### Static allocation

Form: `[(ticker, weight), ...]` + `rebalance_cadence` + `funding`.
Weights must sum to 100%. Tickers must exist in `data/market/ohlcv/`.
Covers 60/40, equal-weight, permanent portfolio, all-VOO, etc.

## Mirror data ingestion

All free, no API keys required.

| Source | Endpoint | Cadence | Notes |
|---|---|---|---|
| Pelosi / STOCK Act | `disclosures-clerk.house.gov/PublicDisclosure/FinancialDisclosure` | Weekly | Scrape PDFs, parse trade rows. |
| Form 4 / Insider | SEC EDGAR RSS feed | Daily | Aggregate by ticker; large insider buys are the signal. |
| 13F (Berkshire, ARK, Scion) | SEC EDGAR XBRL feed | Quarterly | **Filings lag 45 days**, and ingestion must reflect that lag — backtests show holdings only after the legal disclosure date, not the as-of date. |
| Midas agents | `data/portfolios/<agent_id>/snapshots.jsonl` | Already exists | No new ingestion. |

Each source produces a JSONL file with rows of the shape `{date, ticker, action, weight_or_shares, source_metadata}`. The mirror executor reads the JSONL chronologically and rebuilds the target portfolio at each rebalance step.

## API contract

### `POST /run`

Request body:
```json
{
  "kind": "signal" | "mirror" | "allocation",
  "config": { ... shape-specific ... },
  "start_date": "2018-01-01",
  "end_date": "2026-04-25",
  "capital": 10000,
  "currency": "EUR",
  "compare_to": ["msci-world", "coin-flip"]
  // Currency handling: `currency` is the reporting currency for the equity curve.
  // When a strategy holds USD-denominated tickers but reports in EUR, FX conversion
  // uses the same rate source as the existing `engine/` (committed FX series). If
  // FX is unavailable for a date, the response includes a `warnings` array — the
  // backtest still runs, it just falls back to the most recent known rate.
}
```

Response body:
```json
{
  "equity_curve": [{"date": "...", "value": 10000.0}, ...],
  "metrics": {
    "total_return_pct": 47.2,
    "cagr_pct": 8.1,
    "sharpe": 0.91,
    "max_drawdown_pct": -22.4,
    "vs_msci_world_pct": 4.3,
    "vs_coin_flip_pct": 18.7
  },
  "trades": [ ... top 20 by absolute P&L ... ],
  "config_hash": "sha256-...",
  "cached": false
}
```

The frontend hashes the canonical config client-side and checks the static cache before calling `/run`. The `cached` field in the response is informational only.

### `GET /healthz`

Trivial liveness endpoint. The frontend pings this on page load to wake the Cloud Run instance.

## URL persistence

Form state serializes to a base64-encoded JSON config in the querystring. Each strategy in a multi-strategy comparison gets its own `s=` param.

```
/simulate?s=<base64-config-1>&s=<base64-config-2>&s=<base64-config-3>
```

On mount, the page reads `s` params, populates the form(s), runs simulations, renders. Full state is in the URL — every backtest is shareable by copy-paste, no accounts, no DB.

Maximum 3 strategies overlaid on one chart.

## Cache layer

During the existing daily-session pipeline (added as Step 9b, after Step 9a baselines refresh), a new step pre-computes every `(strategy_kind, universe_or_source, default_params)` combo for the default time window (2010-01-01 to today, €10k starting capital, EUR). Output goes to `data/simulations/cache/<config-hash>.json` and is committed alongside the rest of the daily session artifacts.

Frontend computes the same hash from form state and tries `data/simulations/cache/<hash>.json` first via static fetch. Hit = instant render. Miss = fall through to Cloud Run.

Estimated cache size at v1: 11 signal strategies × 10 universes + 6 mirror sources + ~5 default allocations ≈ 121 entries. Each entry is ~50KB JSON, so ~6 MB committed — comparable to the existing portfolios cache.

## Frontend

New section under `site/src/pages/simulate/`. Astro shells with a client-hydrated form and chart island.

### Pages
- `/simulate` — landing with prebuilt example backtests visible immediately + the builder.
- `/simulate?s=...` — same page, querystring drives form state.

### Form
- Top-level: "strategy kind" picker (signal / mirror / allocation).
- Sub-form changes based on kind.
- Below: dates, capital, currency, rebalance cadence (where applicable), benchmarks toggle.
- "Add strategy" button (up to 3) for overlay comparisons.

### Chart
The existing site charts are hand-rolled inline SVG. The simulator needs hover/tooltip/legend/multi-series overlay, which makes inline SVG painful. **Open implementation question:** which lightweight chart library to introduce. Candidates: `uPlot` (smallest, fastest), `Chart.js`, `Plotly` (heaviest but most batteries-included). Pick during planning — should be the most lightweight option that supports multi-series + hover.

### Cold-start UX
On page load, frontend pings `/healthz` to wake Cloud Run. While the user is filling the form, the backend is warming up. If the user submits before warmup completes, show a "warming up — first backtest takes a moment" loader. Subsequent submits are warm and fast.
A pre-rendered example backtest is visible from first paint, so the page never feels empty.

## Output metrics

Same set as agent dossiers, for visual and conceptual consistency:

- Equity curve (daily resolution)
- Total return %, CAGR %, Sharpe (annualized), max drawdown %
- Vs MSCI World benchmark delta (% absolute)
- Vs coin-flip baseline delta (% absolute)
- Top 20 trades by absolute P&L (date, ticker, side, qty, price, P&L)

When multiple strategies are overlaid, each gets its own row of metrics under the chart.

## Testing

- `backtester/tests/` — unit tests for each strategy shape's executor, mirror-source loaders, config hashing.
- Property tests for: capital scaling linearity (curve at €1k vs €10k differs only by scalar), config-hash determinism (same config → same hash regardless of key order), date-window monotonicity (earlier start ⇒ longer or equal curve).
- Integration test: each cached config in `data/simulations/cache/` must be reproducible by `POST /run` with the same input, byte-identical.
- Site: smoke test that `/simulate` renders, that a known cached config produces the expected curve, that the form ↔ URL round-trip preserves state.

## Operational notes

- **Daily session impact:** adds Step 9b (cache refresh) after Step 9a (baselines). Same load-bearing pattern: a daily commit touching `data/portfolios/` without `data/simulations/cache/` indicates Step 9b was skipped.
- **Disclosure ingestion:** separate workflow (`fetch-disclosures.yml`), independent cadences. Failures here do not block daily sessions; they just make the corresponding mirror sources stale.
- **Cloud Run cost monitoring:** set a billing alert at $1/month. The free tier is generous enough that paid usage signals either viral traffic (good problem) or a runaway loop (must fix).
- **Zero-cost compliance:** Cloud Run free tier + GitHub Actions + static site = no recurring spend.

## Implementation phasing

The plan generated from this spec should phase in this order:

1. `backtester/` skeleton — FastAPI app, `/healthz`, signal-shape executor wrapping existing `engine/` code. Local dev only.
2. Cloud Run deployment — Dockerfile, `gcloud run deploy`, custom domain.
3. `/simulate` page — form for signal-shape only, URL persistence, chart lib chosen, basic comparison overlay.
4. Static cache layer — config hashing client+server, daily session Step 9b.
5. Mirror shape — agent-mirror first (no ingestion needed), then 13F (slowest cadence, simplest format), then Form 4, then Pelosi.
6. Allocation shape — small, ship last.
7. Polish — cold-start UX, mobile responsive pass, prebuilt example gallery.

Each phase is independently testable and shippable.
