# Ticker name registry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display the company / asset name behind every ticker the site shows, sourced from a committed `data/tickers.json` registry that the existing OHLCV workflow keeps fresh.

**Architecture:** Add a pure-function Python module `engine/tickers.py` that handles registry I/O, idempotent merge, and yfinance-info → (name, type) resolution. Wire it into `scripts/fetch_ohlcv.py` as a best-effort step alongside the OHLCV fetch (with a `--names-only` switch for the one-time bootstrap). Mirror a thin TypeScript reader in `site/src/lib/tickers.ts` and surface the name on the `/ticker/[slug]` page header plus as a `title=` tooltip on the ticker chips in `TradeCard` and `PortfolioTable`. When the registry has no name for a symbol, the site silently falls back to today's symbol-only rendering — no regression.

**Tech Stack:** Python 3.12 + yfinance + pytest; Astro + TypeScript.

**Spec:** `docs/superpowers/specs/2026-05-14-ticker-name-registry-design.md`

---

## File Structure

**New:**
- `engine/tickers.py` — Registry I/O, merge, name/type resolver. Pure functions, no side effects beyond file write in `save_registry`.
- `tests/test_tickers.py` — Pytest unit tests for the module.
- `data/tickers.json` — Committed registry, populated by the bootstrap task.
- `site/src/lib/tickers.ts` — Build-time reader for the site.

**Modified:**
- `scripts/fetch_ohlcv.py` — After each symbol's OHLCV download, also fetch `Ticker(symbol).info` and merge into the registry. Adds `--names-only` flag.
- `site/src/pages/ticker/[slug].astro` — Render the name as a subtitle under `<h1>`.
- `site/src/components/TradeCard.astro` — `title={tickerName(o.ticker) ?? undefined}` on the ticker anchor.
- `site/src/components/PortfolioTable.astro` — Same treatment on the ticker cell.

**Not touched:** No new GitHub Actions workflow — the existing `fetch-ohlcv.yml` cron picks up the registry refresh automatically once the populator is wired in.

---

### Task 1: Registry I/O and merge — TDD

**Files:**
- Create: `engine/tickers.py`
- Test: `tests/test_tickers.py`

- [ ] **Step 1: Write the failing tests for registry round-trip + merge**

Create `tests/test_tickers.py`:

```python
"""Tests for engine.tickers — registry I/O, idempotent merge, name resolution."""

from pathlib import Path

from engine.tickers import (
    load_registry,
    save_registry,
    merge,
    resolve_name,
)


def test_load_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    assert load_registry(path=tmp_path / "nope.json") == {}


def test_round_trip_preserves_data(tmp_path: Path) -> None:
    reg = {
        "AAPL": {"name": "Apple Inc.", "type": "equity"},
        "VOO": {"name": "Vanguard S&P 500 ETF", "type": "etf"},
    }
    path = tmp_path / "tickers.json"
    save_registry(reg, path=path)
    assert load_registry(path=path) == reg


def test_merge_adds_new_entry() -> None:
    existing = {"AAPL": {"name": "Apple Inc.", "type": "equity"}}
    fresh = {"MSFT": {"name": "Microsoft Corporation", "type": "equity"}}
    out = merge(existing, fresh)
    assert out["AAPL"] == {"name": "Apple Inc.", "type": "equity"}
    assert out["MSFT"] == {"name": "Microsoft Corporation", "type": "equity"}


def test_merge_keeps_existing_when_fresh_name_is_null() -> None:
    existing = {"AAPL": {"name": "Apple Inc.", "type": "equity"}}
    fresh = {"AAPL": {"name": None, "type": "unknown"}}
    out = merge(existing, fresh)
    assert out["AAPL"] == {"name": "Apple Inc.", "type": "equity"}


def test_merge_replaces_existing_when_fresh_name_is_non_null() -> None:
    existing = {"AAPL": {"name": None, "type": "unknown"}}
    fresh = {"AAPL": {"name": "Apple Inc.", "type": "equity"}}
    out = merge(existing, fresh)
    assert out["AAPL"] == {"name": "Apple Inc.", "type": "equity"}


def test_merge_overwrites_existing_when_fresh_name_changes() -> None:
    existing = {"X": {"name": "Old Name", "type": "equity"}}
    fresh = {"X": {"name": "New Name", "type": "equity"}}
    out = merge(existing, fresh)
    assert out["X"] == {"name": "New Name", "type": "equity"}


def test_merge_preserves_keys_only_in_existing() -> None:
    existing = {"AAPL": {"name": "Apple Inc.", "type": "equity"}}
    fresh = {"MSFT": {"name": "Microsoft Corporation", "type": "equity"}}
    out = merge(existing, fresh)
    assert "AAPL" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tickers.py -v`
Expected: ImportError — `engine.tickers` does not exist yet.

- [ ] **Step 3: Implement registry I/O and merge**

Create `engine/tickers.py`:

```python
"""Ticker name registry — maps symbol → human-readable name + asset type.

The registry is committed to git at data/tickers.json so the site can read it
at build time and so the sandboxed daily-session agent can see it. It is
populated and refreshed by scripts/fetch_ohlcv.py, which already calls
yfinance for every symbol once a week.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = _PROJECT_ROOT / "data" / "tickers.json"


class TickerInfo(TypedDict):
    name: str | None
    type: str  # "equity" | "etf" | "crypto" | "forex" | "unknown"


Registry = dict[str, TickerInfo]


def load_registry(path: Path = DEFAULT_PATH) -> Registry:
    """Load the registry from disk. Returns {} when the file is missing."""
    if not path.exists():
        return {}
    with path.open() as f:
        return json.load(f)


def save_registry(reg: Registry, path: Path = DEFAULT_PATH) -> None:
    """Write the registry to disk, sorted by symbol for diff stability."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {k: reg[k] for k in sorted(reg)}
    with path.open("w") as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)
        f.write("\n")


def merge(existing: Registry, fresh: Registry) -> Registry:
    """Merge a freshly-fetched registry into the existing one.

    Rule: when ``fresh[key].name`` is ``None``, keep the existing entry
    intact (a transient yfinance failure must not blank out a known name).
    Otherwise replace the existing entry wholesale.
    """
    out: Registry = dict(existing)
    for key, info in fresh.items():
        if info.get("name") is None and key in out and out[key].get("name") is not None:
            continue
        out[key] = info
    return out
```

- [ ] **Step 4: Run tests to verify the merge / round-trip tests pass**

Run: `pytest tests/test_tickers.py -v -k "round_trip or merge or load_returns_empty"`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/tickers.py tests/test_tickers.py
git commit -m "feat(tickers): registry I/O with idempotent merge"
```

---

### Task 2: Name + type resolver — TDD

**Files:**
- Modify: `engine/tickers.py`
- Test: `tests/test_tickers.py`

- [ ] **Step 1: Append failing tests for resolve_name**

Append to `tests/test_tickers.py`:

```python
def test_resolve_uses_long_name_when_present() -> None:
    info = {"longName": "Apple Inc.", "shortName": "Apple", "quoteType": "EQUITY"}
    assert resolve_name("AAPL", info) == {"name": "Apple Inc.", "type": "equity"}


def test_resolve_falls_back_to_short_name_when_long_empty() -> None:
    info = {"longName": "", "shortName": "Microsoft", "quoteType": "EQUITY"}
    assert resolve_name("MSFT", info) == {"name": "Microsoft", "type": "equity"}


def test_resolve_treats_etf_quote_type() -> None:
    info = {"longName": "Vanguard S&P 500 ETF", "quoteType": "ETF"}
    assert resolve_name("VOO", info) == {"name": "Vanguard S&P 500 ETF", "type": "etf"}


def test_resolve_crypto_usd_from_static_map_when_info_missing() -> None:
    assert resolve_name("BTC-USD", None) == {"name": "Bitcoin", "type": "crypto"}


def test_resolve_crypto_eur_from_static_map_when_info_missing() -> None:
    assert resolve_name("ETH-EUR", None) == {"name": "Ethereum", "type": "crypto"}


def test_resolve_crypto_unknown_base_returns_unknown_name() -> None:
    # WIF-USD: real coin, not in the static map. We must not invent a name.
    assert resolve_name("WIF-USD", None) == {"name": None, "type": "crypto"}


def test_resolve_forex_pattern() -> None:
    assert resolve_name("EURUSD=X", None) == {"name": "EUR/USD", "type": "forex"}


def test_resolve_unknown_symbol_with_no_info() -> None:
    assert resolve_name("MYSTERY", None) == {"name": None, "type": "unknown"}


def test_resolve_prefers_yfinance_name_over_static_map() -> None:
    # If yfinance has a richer name for a crypto, use it.
    info = {"longName": "Bitcoin USD", "quoteType": "CRYPTOCURRENCY"}
    assert resolve_name("BTC-USD", info) == {"name": "Bitcoin USD", "type": "crypto"}


def test_resolve_currency_quote_type_maps_to_forex() -> None:
    info = {"longName": "EUR/USD", "quoteType": "CURRENCY"}
    assert resolve_name("EURUSD=X", info) == {"name": "EUR/USD", "type": "forex"}


def test_resolve_ignores_empty_string_long_name() -> None:
    info = {"longName": "   ", "shortName": "BTC", "quoteType": "CRYPTOCURRENCY"}
    assert resolve_name("BTC-USD", info) == {"name": "BTC", "type": "crypto"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tickers.py -v -k resolve`
Expected: FAIL — `resolve_name` is not defined.

- [ ] **Step 3: Implement resolve_name**

Append to `engine/tickers.py`:

```python
import re

_CRYPTO_PATTERN = re.compile(r"^([A-Z]{2,6})-(USD|EUR)$")
_FOREX_PATTERN = re.compile(r"^([A-Z]{3})([A-Z]{3})=X$")

_CRYPTO_NAMES: dict[str, str] = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "XRP": "XRP",
    "ADA": "Cardano",
    "DOGE": "Dogecoin",
    "LTC": "Litecoin",
    "BCH": "Bitcoin Cash",
    "LINK": "Chainlink",
    "DOT": "Polkadot",
    "AVAX": "Avalanche",
    "MATIC": "Polygon",
    "ATOM": "Cosmos",
    "XLM": "Stellar",
    "TRX": "TRON",
    "UNI": "Uniswap",
}

_QUOTE_TYPE_MAP = {
    "EQUITY": "equity",
    "ETF": "etf",
    "MUTUALFUND": "etf",
    "CRYPTOCURRENCY": "crypto",
    "CURRENCY": "forex",
}


def _infer_type(symbol: str, info: dict | None) -> str:
    if info:
        qt = (info.get("quoteType") or "").upper()
        if qt in _QUOTE_TYPE_MAP:
            return _QUOTE_TYPE_MAP[qt]
    if _CRYPTO_PATTERN.match(symbol):
        return "crypto"
    if _FOREX_PATTERN.match(symbol):
        return "forex"
    return "unknown"


def _yfinance_name(info: dict | None) -> str | None:
    if not info:
        return None
    for key in ("longName", "shortName"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _shape_name(symbol: str) -> str | None:
    crypto_match = _CRYPTO_PATTERN.match(symbol)
    if crypto_match:
        base = crypto_match.group(1)
        return _CRYPTO_NAMES.get(base)  # may be None for unknown coins
    forex_match = _FOREX_PATTERN.match(symbol)
    if forex_match:
        return f"{forex_match.group(1)}/{forex_match.group(2)}"
    return None


def resolve_name(symbol: str, info: dict | None) -> TickerInfo:
    """Resolve a ticker symbol to its (name, type) entry.

    Resolution order for ``name``:
      1. yfinance ``longName`` if non-empty.
      2. yfinance ``shortName`` if non-empty.
      3. Symbol-shape heuristic (crypto static map, forex pair formatter).
      4. ``None``.

    ``type`` is taken from ``info['quoteType']`` when available, otherwise
    inferred from the symbol shape.
    """
    name = _yfinance_name(info) or _shape_name(symbol)
    return {"name": name, "type": _infer_type(symbol, info)}
```

- [ ] **Step 4: Run all tests in this file to verify they pass**

Run: `pytest tests/test_tickers.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/tickers.py tests/test_tickers.py
git commit -m "feat(tickers): symbol → name resolver with yfinance + shape fallback"
```

---

### Task 3: Wire the populator into `scripts/fetch_ohlcv.py`

**Files:**
- Modify: `scripts/fetch_ohlcv.py`

- [ ] **Step 1: Add the registry imports and a per-symbol helper**

Open `scripts/fetch_ohlcv.py`. After the `import yfinance as yf` line (around line 29), add:

```python
from engine.tickers import (
    DEFAULT_PATH as _TICKERS_PATH,
    load_registry,
    merge as _merge_registry,
    resolve_name,
    save_registry,
)
```

After the `_PORTFOLIOS_DIR = ...` line (around line 58), add:

```python
def _fetch_ticker_info(symbol: str) -> dict | None:
    """Fetch yfinance .info for a symbol. Returns None on any failure.

    Names are best-effort — a yfinance hiccup must never fail the OHLCV run.
    """
    try:
        return yf.Ticker(symbol).info  # type: ignore[no-any-return]
    except Exception as exc:
        print(f"  ! {symbol}: info fetch error — {exc}", file=sys.stderr)
        return None
```

- [ ] **Step 2: Add the `--names-only` flag and the names-update step**

In `main()`, just before the `args = parser.parse_args()` line (~line 317), add the new flag:

```python
    parser.add_argument(
        "--names-only",
        action="store_true",
        help=(
            "Skip OHLCV download — only refresh the data/tickers.json "
            "registry. Used for the one-time bootstrap and for cheap "
            "re-runs after a universe change."
        ),
    )
```

Then replace the body of `main()` after symbol resolution (from the `print(f"Resolved {len(symbols)} symbols to fetch.")` line through the final `print(f"\nDone..."` and `return 0`) with this block. The full replacement is below — copy verbatim:

```python
    print(f"Resolved {len(symbols)} symbols to fetch.")
    if args.dry_run:
        for s in symbols:
            print(f"  {s}")
        return 0

    end = date.today()
    default_start = end - timedelta(days=args.history_days)

    registry_updates: dict[str, dict] = {}

    total_new = 0
    failures = 0
    for i, symbol in enumerate(symbols, start=1):
        if not args.names_only:
            path = _OHLCV_DIR / f"{symbol}.jsonl"
            if args.backfill:
                start = default_start
            elif path.exists():
                existing = _existing_dates(path)
                if existing:
                    last = max(datetime.fromisoformat(d).date() for d in existing)
                    if last >= end - timedelta(days=1):
                        registry_updates[symbol] = resolve_name(symbol, _fetch_ticker_info(symbol))
                        continue  # OHLCV already up to date; still refresh name
                    start = last + timedelta(days=1)
                else:
                    start = default_start
            else:
                start = default_start

            df = _fetch_symbol(symbol, start, end)
            if df is None:
                failures += 1
            else:
                n = _write_rows(symbol, df)
                total_new += n
                if i % 25 == 0 or n > 0:
                    print(f"  [{i}/{len(symbols)}] {symbol}: +{n} rows")

        registry_updates[symbol] = resolve_name(symbol, _fetch_ticker_info(symbol))

    if registry_updates:
        existing_reg = load_registry()
        merged = _merge_registry(existing_reg, registry_updates)
        save_registry(merged)
        non_null = sum(1 for v in registry_updates.values() if v.get("name"))
        print(
            f"Refreshed tickers registry: {non_null}/{len(registry_updates)} "
            f"symbols resolved to a name."
        )

    if args.names_only:
        print(f"\nDone (names-only).")
    else:
        print(
            f"\nDone. Wrote {total_new} new rows across {len(symbols)} "
            f"symbols. {failures} failures."
        )
    return 0
```

- [ ] **Step 3: Smoke-test on a tiny symbol set without writing the registry**

Run: `python scripts/fetch_ohlcv.py --names-only --symbols AAPL,VOO,BTC-USD,EURUSD=X,MYSTERY`
Expected: prints "Refreshed tickers registry: 4/5 symbols resolved to a name." (MYSTERY does not resolve.) Verify `data/tickers.json` now exists and contains the four resolved entries plus `MYSTERY` with `name: null`.

- [ ] **Step 4: Verify dry-run still works**

Run: `python scripts/fetch_ohlcv.py --dry-run --symbols AAPL,VOO`
Expected: lists the two symbols, exits 0, registry untouched.

- [ ] **Step 5: Roll back the smoke-test registry**

```bash
git checkout -- data/tickers.json 2>/dev/null || rm -f data/tickers.json
```

Expected: working tree clean for `data/tickers.json`. The real bootstrap happens in Task 4.

- [ ] **Step 6: Commit the populator wiring**

```bash
git add scripts/fetch_ohlcv.py
git commit -m "feat(fetch-ohlcv): populate data/tickers.json alongside OHLCV"
```

---

### Task 4: Bootstrap `data/tickers.json`

**Files:**
- Create: `data/tickers.json` (via script run)

- [ ] **Step 1: Run the names-only bootstrap over the full universe**

Run: `python scripts/fetch_ohlcv.py --names-only`
Expected: takes ~5-8 minutes; prints "Refreshed tickers registry: N/M symbols resolved to a name." where N is most of M. Some forex / index symbols may not resolve to a name — that's expected.

- [ ] **Step 2: Spot-check the output**

Inspect `data/tickers.json` and confirm:
- `"AAPL"` has a non-null name containing "Apple"
- `"VOO"` has a non-null name containing "Vanguard"
- `"BTC-USD"` has a non-null name (yfinance or the static "Bitcoin" fallback)
- `"EURUSD=X"` has `"name": "EUR/USD"` or similar
- Indices like `"^VIX"` may have `name: null, type: "unknown"` — acceptable

If any of the four spot-check tickers has a null name, re-run the bootstrap (transient yfinance failure). If they still fail, investigate before committing.

- [ ] **Step 3: Commit the bootstrap output**

```bash
git add data/tickers.json
git commit -m "chore(tickers): bootstrap registry from yfinance"
```

---

### Task 5: TypeScript reader — `site/src/lib/tickers.ts`

**Files:**
- Create: `site/src/lib/tickers.ts`

- [ ] **Step 1: Implement the reader**

Create `site/src/lib/tickers.ts`:

```typescript
import * as fs from "node:fs";
import * as path from "node:path";
import { DATA_DIR } from "./paths";

const REGISTRY_FILE = path.join(DATA_DIR, "tickers.json");

type TickerInfo = {
  name: string | null;
  type: "equity" | "etf" | "crypto" | "forex" | "unknown";
};

type Registry = Record<string, TickerInfo>;

let cached: Registry | null = null;

function loadRegistry(): Registry {
  if (cached) return cached;
  if (!fs.existsSync(REGISTRY_FILE)) {
    cached = {};
    return cached;
  }
  cached = JSON.parse(fs.readFileSync(REGISTRY_FILE, "utf-8")) as Registry;
  return cached;
}

export function tickerName(symbol: string): string | null {
  return loadRegistry()[symbol]?.name ?? null;
}

export function tickerType(symbol: string): TickerInfo["type"] {
  return loadRegistry()[symbol]?.type ?? "unknown";
}
```

- [ ] **Step 2: Verify the file type-checks**

Run from the repo root:
```
cd site && npx astro check 2>&1 | head -40
```
Expected: no errors mentioning `tickers.ts`. Pre-existing warnings elsewhere are fine.

- [ ] **Step 3: Commit**

```bash
git add site/src/lib/tickers.ts
git commit -m "feat(site): tickers.ts build-time reader for the registry"
```

---

### Task 6: Show the name on the ticker page

**Files:**
- Modify: `site/src/pages/ticker/[slug].astro`

- [ ] **Step 1: Add the name subtitle**

Add the import at the top of the frontmatter (after the existing `import { isTradingAgent, getAgent } from "@/lib/roster";` line):

```typescript
import { tickerName } from "@/lib/tickers";
```

Then add this line in the frontmatter just below the existing `const { slug } = Astro.params as { slug: string };`:

```typescript
const name = tickerName(ticker);
```

In the `<header>` block, replace:

```astro
      <h1>{ticker}</h1>
      <p class="lede">
        Every order any agent wrote for this ticker, filled or rejected. One row per order.
      </p>
```

with:

```astro
      <h1>{ticker}</h1>
      {name && <p class="name">{name}</p>}
      <p class="lede">
        Every order any agent wrote for this ticker, filled or rejected. One row per order.
      </p>
```

In the `<style>` block, add a `.name` rule directly after the existing `.ticker-page h1` rule:

```css
  .ticker-page .name { font-size: var(--fs-md); color: var(--ink-muted); margin: 0 0 1.25rem; font-style: italic; }
```

- [ ] **Step 2: Build the site to verify**

Run: `cd site && npm run build`
Expected: build succeeds. Spot-check the output of any known ticker page, e.g. `site/dist/ticker/AAPL/index.html` should contain `<p class="name">Apple Inc.</p>` (or whatever yfinance returned).

- [ ] **Step 3: Commit**

```bash
git add site/src/pages/ticker/[slug].astro
git commit -m "feat(site): show company name on /ticker/[slug] page"
```

---

### Task 7: Tooltips on ticker chips

**Files:**
- Modify: `site/src/components/TradeCard.astro`
- Modify: `site/src/components/PortfolioTable.astro`

- [ ] **Step 1: Add the tooltip in TradeCard**

In `site/src/components/TradeCard.astro`, replace the import line:

```typescript
import { tickerSlug } from "@/lib/orders";
```

with:

```typescript
import { tickerSlug } from "@/lib/orders";
import { tickerName } from "@/lib/tickers";
```

Then replace the ticker anchor line:

```astro
        <a class="tick" href={`/ticker/${tickerSlug(o.ticker)}`}>{o.ticker}</a>
```

with:

```astro
        <a class="tick" href={`/ticker/${tickerSlug(o.ticker)}`} title={tickerName(o.ticker) ?? undefined}>{o.ticker}</a>
```

- [ ] **Step 2: Add the tooltip in PortfolioTable**

In `site/src/components/PortfolioTable.astro`, replace the import line:

```typescript
import { tickerSlug } from "@/lib/orders";
```

with:

```typescript
import { tickerSlug } from "@/lib/orders";
import { tickerName } from "@/lib/tickers";
```

Then replace the ticker cell:

```astro
              <td><a class="tick" href={`/ticker/${tickerSlug(r.ticker)}`}>{r.ticker}</a></td>
```

with:

```astro
              <td><a class="tick" href={`/ticker/${tickerSlug(r.ticker)}`} title={tickerName(r.ticker) ?? undefined}>{r.ticker}</a></td>
```

- [ ] **Step 3: Rebuild and verify**

Run: `cd site && npm run build`
Expected: build succeeds.

Spot-check: grep one of the built feed pages for a `title=` attribute on a `.tick` anchor:
```
grep -o 'class="tick"[^>]*title="[^"]*"' site/dist/feed/index.html | head -3
```
Expected: one or more matches showing `title="Apple Inc."`-style attributes.

- [ ] **Step 4: Commit**

```bash
git add site/src/components/TradeCard.astro site/src/components/PortfolioTable.astro
git commit -m "feat(site): ticker-name tooltip on trade card + portfolio chips"
```

---

### Task 8: Final verification

**Files:** none modified.

- [ ] **Step 1: Run the full Python test suite**

Run: `pytest tests/ -q`
Expected: all green. Pre-existing tests untouched; new `tests/test_tickers.py` tests included.

- [ ] **Step 2: Run the site type-check**

Run: `cd site && npx astro check`
Expected: no new errors versus baseline.

- [ ] **Step 3: Manual browser eyeball**

Run: `cd site && npm run dev` and open:
- `http://localhost:4321/ticker/AAPL` — name "Apple Inc." (or similar) appears below the symbol.
- `http://localhost:4321/feed` — hover any ticker chip in a trade card; the browser tooltip shows the company name.
- `http://localhost:4321/arena/world` (or any agent dossier with positions) — hover a row in the portfolio table; tooltip shows the name.
- A ticker known to have `name: null` (pick one from `data/tickers.json`, e.g. `^VIX`): the page header has no subtitle, the chip has no tooltip, no visual regression.

- [ ] **Step 4: Stop the dev server.**
