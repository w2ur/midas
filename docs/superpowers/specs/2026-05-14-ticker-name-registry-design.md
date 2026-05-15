# Ticker name registry

**Date:** 2026-05-14
**Status:** Approved, ready for plan

## Problem

The site shows raw ticker symbols everywhere: trade cards (`BKR`), portfolio
tables (`CPAY · 4 shares`), and the dedicated `/ticker/[slug]` page header.
A visitor reading "Satoshi bought 12 shares of BKR" cannot follow the
narrative without recognising the company. The same friction exists for the
operator reviewing dossiers. Universes are bare symbol arrays
(`data/universes/*.json`), and `data/ticker_currencies.json` only carries
three currency overrides — there is no human-readable name anywhere in the
data layer.

## Goal

Surface the company / asset name behind every ticker the site displays,
without introducing runtime network calls or duplicating data already held
elsewhere.

## Approach

Add a committed registry `data/tickers.json` keyed by symbol. Populate it
opportunistically from the existing OHLCV fetch workflow (which already
calls yfinance for every symbol once a week). Read the registry at site
build time and:

- Render the name as a subtitle on `/ticker/[slug]`.
- Surface the name as a `title=` tooltip on the ticker chip in `TradeCard`
  and on the ticker cell in `PortfolioTable`.

No new workflow, no new cron, no new Python service. The OHLCV job is the
canonical "did we see this ticker" signal, so the registry stays in step
with the universe automatically.

## Data shape

`data/tickers.json` is a flat object:

```json
{
  "AAPL":     { "name": "Apple Inc.",                "type": "equity" },
  "VOO":      { "name": "Vanguard S&P 500 ETF",      "type": "etf"    },
  "BTC-USD":  { "name": "Bitcoin",                   "type": "crypto" },
  "EURUSD=X": { "name": "EUR/USD",                   "type": "forex"  },
  "EXOTIC.X": { "name": null,                        "type": "unknown"}
}
```

- `name` is `string | null`. `null` means the populator could not resolve a
  human-readable name; the site falls back to showing the symbol alone
  (no regression vs today).
- `type` is one of `"equity" | "etf" | "crypto" | "forex" | "unknown"`,
  derived from the symbol shape and yfinance metadata. Used to drive the
  fallback formatter and to allow future filtering on the dashboard
  without a follow-up migration.

The registry is committed. It is **append-only with idempotent merge**:
a refresh that fails to fetch a name for a symbol that already has one
must not overwrite the existing name with `null`. This keeps the
registry resilient to yfinance hiccups.

## Naming rules and fallbacks

Per-symbol resolution, in order:

1. **yfinance `info["longName"]`** if non-empty.
2. **yfinance `info["shortName"]`** if `longName` is empty.
3. **Symbol-shape heuristic** for symbols yfinance cannot describe:
   - `^[A-Z]{3,4}-USD$` or `-EUR$` → strip suffix, look up in a small
     static crypto map (`BTC`, `ETH`, `SOL`, `XRP`, `ADA`, `DOGE`,
     `LTC`, `BCH`, `LINK`, `DOT`, `AVAX`, `MATIC`, `ATOM`, `XLM`,
     `TRX`, `UNI`). Format as `"<Name>"`. The trailing `-USD` / `-EUR`
     already tells the reader the quote currency; repeating it is noise.
   - `^[A-Z]{6}=X$` → format as `"AAA/BBB"` (e.g. `EURUSD=X` → `EUR/USD`).
   - Anything else → `name = null`, `type = "unknown"`.

Type inference uses the same shape rules plus yfinance
`info["quoteType"]` when available (`"EQUITY"`, `"ETF"`, `"CRYPTOCURRENCY"`,
`"CURRENCY"`).

## Components

### New

- **`data/tickers.json`** — the registry. Committed.
- **`engine/tickers.py`** — Python helpers:
  - `load_registry() -> dict[str, TickerInfo]`
  - `save_registry(reg: dict[str, TickerInfo]) -> None`
  - `merge(existing, fresh) -> dict[str, TickerInfo]` — idempotent merge
    that preserves existing non-null names when `fresh` carries `null`.
  - `resolve_name(symbol: str, info: dict | None) -> tuple[name, type]` —
    pure function implementing the resolution rules above; takes the
    yfinance `info` dict (or `None`) and returns the final tuple.
- **`site/src/lib/tickers.ts`** — build-time reader:
  - `loadTickerRegistry()` (cached per build)
  - `tickerName(symbol: string): string | null`
  - `tickerType(symbol: string): "equity"|"etf"|"crypto"|"forex"|"unknown"`

### Modified

- **`scripts/fetch_ohlcv.py`** — after fetching OHLCV for each symbol,
  also call `yf.Ticker(symbol).info`, resolve via
  `engine.tickers.resolve_name`, and merge into the registry. Wrapped
  in try/except so a names failure never fails the OHLCV run. Adds a
  `--names-only` flag for the one-time bootstrap (skip OHLCV, just
  refresh names).
- **`site/src/pages/ticker/[slug].astro`** — render the name as a
  subtitle directly under the `<h1>{ticker}</h1>`. When `name` is
  `null`, omit the subtitle entirely. No layout shift.
- **`site/src/components/TradeCard.astro`** — add
  `title={tickerName(o.ticker) ?? undefined}` to the ticker anchor.
- **`site/src/components/PortfolioTable.astro`** — same treatment on
  the ticker cell.

### Not touched

- No new GitHub Actions workflow. The existing `fetch-ohlcv.yml` cron
  does the refresh.
- `LeaderboardTable.astro`, `BaselineChart.astro`, and `PostItem.astro`
  do not surface raw tickers — no changes there.

## Bootstrap

The first commit lands `data/tickers.json` populated locally via:

```
python scripts/fetch_ohlcv.py --names-only
```

This iterates every symbol the OHLCV fetcher already knows about
(union of universes + portfolios), resolves names via yfinance, and
writes the registry. Subsequent weekly OHLCV runs maintain it.

## Testing

`tests/test_tickers.py`:

- Registry round-trip: write → read returns identical dict.
- Idempotent merge: existing `{"AAPL": {"name": "Apple Inc.", ...}}`
  merged with fresh `{"AAPL": {"name": null, ...}}` keeps the existing
  name.
- Idempotent merge: existing null is replaced by a fresh non-null name.
- `resolve_name`:
  - yfinance `longName` wins.
  - `shortName` used when `longName` is empty.
  - Crypto pairs (`BTC-USD`, `ETH-EUR`) resolve via the static map
    even when `info` is `None`.
  - Forex pairs (`EURUSD=X`) format as `"EUR/USD"` regardless of `info`.
  - Unknown symbols return `(None, "unknown")`.

The existing OHLCV test suite stays green — the names step is additive
and exception-isolated.

## Performance

yfinance `info` requests are slower than `history()` (roughly 0.3-0.8s
each). For the ~600-ticker universe this adds ~3-8 minutes to the
weekly fetch. Acceptable: this runs once a week in CI, not in the
trading-hour critical path. If it ever becomes a bottleneck, the
populator can short-circuit symbols where the registry already holds
a non-null name from a recent run — but that optimisation stays out
of v1.

## Out of scope (intentionally)

- **No sector / industry / market-cap metadata.** Just name + type.
- **No description / blurb.** Name alone answers "what is this".
- **No icons or logos.** Licensing surface, aesthetic bikeshed.
- **No live price or fundamentals.** Midas is a story site, not a
  quote terminal.
- **No localisation.** Names render in their canonical English form.
- **No retroactive renaming.** If a company rebrands, the next weekly
  fetch picks it up; we do not maintain a history.
