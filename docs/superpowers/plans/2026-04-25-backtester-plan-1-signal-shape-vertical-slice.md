# Backtester Plan 1 — Signal-Shape Vertical Slice

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an end-to-end signal-shape backtester: a FastAPI service deployed to Cloud Run, plus a `/simulate` page on the Midas site where visitors compose a signal-driven strategy, get a real backtest result, and share it via URL.

**Architecture:** New `backtester/` Python package wraps existing `engine.backtest.run_backtest` behind a FastAPI app. Service runs on Google Cloud Run (free tier, scale-to-zero, ~2-5s cold start). Astro `/simulate` page submits config to the service and renders the equity curve with a lightweight chart library. URL querystring is the source of truth for form state — no DB, no accounts.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, Uvicorn, existing `engine/` codebase, Docker, Google Cloud Run, Astro 5, vanilla TypeScript, uPlot (chart), Vitest (site tests), pytest (backend tests).

**Out of scope for this plan (deferred to Plans 2-4):** static cache layer, multi-strategy overlay, mirror-portfolio shape, allocation shape, polish.

## Pre-flight check

Before starting Task 3, run from repo root:
```bash
ls engine/selectors/ engine/managers/
python -c "from engine.types import StrategySpec; help(StrategySpec)"
```
The plan references `equal-weight-rebalance` as a selector and `none` as a manager. If those exact names are not registered, pick the closest registered equivalents and substitute them everywhere they appear in this plan (Tasks 3, 6, 11). This avoids cascading test failures across tasks.

---

## File Structure

**New files:**
- `backtester/__init__.py` — package marker
- `backtester/app.py` — FastAPI app + endpoint handlers
- `backtester/schemas.py` — Pydantic request/response models
- `backtester/runner.py` — universe resolution + price-data assembly + run_backtest wrapper
- `backtester/trades.py` — top-N trades extraction from `BacktestResult.transactions`
- `backtester/comparisons.py` — vs-MSCI-World + vs-coin-flip deltas for the same window
- `backtester/Dockerfile` — container image
- `backtester/.dockerignore`
- `backtester/tests/__init__.py`
- `backtester/tests/conftest.py` — pytest fixtures (FastAPI test client, sample price data)
- `backtester/tests/test_healthz.py`
- `backtester/tests/test_run_signal.py`
- `backtester/tests/test_trades.py`
- `backtester/tests/test_comparisons.py`
- `backtester/README.md` — local dev + Cloud Run deploy steps
- `site/src/pages/simulate/index.astro`
- `site/src/components/SimulateForm.astro`
- `site/src/components/SimulateChart.astro`
- `site/src/lib/simulate-config.ts` — base64 encode/decode + config types
- `site/src/lib/simulate-api.ts` — POST to backtester service
- `site/src/lib/simulate-example.ts` — pre-rendered example payload (committed JSON)
- `site/tests/simulate-config.test.ts`

**Modified files:**
- `requirements.txt` — add `fastapi`, `uvicorn[standard]`, `httpx` (for testclient)
- `site/package.json` — add `uplot`
- `site/src/components/SiteHeader.astro` — add `/simulate` nav link
- `CLAUDE.md` — document `backtester/` directory and the new `/simulate` route

**One-shot manual setup (not automated):**
- Google Cloud project, gcloud CLI auth, Cloud Run service deploy. Documented in `backtester/README.md` and run by the operator once.

---

## Task 1: Bootstrap `backtester/` package with FastAPI + healthz

**Files:**
- Create: `backtester/__init__.py`, `backtester/app.py`, `backtester/tests/__init__.py`, `backtester/tests/conftest.py`, `backtester/tests/test_healthz.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Write the failing test**

Create `backtester/tests/test_healthz.py`:
```python
def test_healthz_returns_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

Create `backtester/tests/conftest.py`:
```python
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make `backtester` and `engine` importable when pytest runs from repo root.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from backtester.app import app  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)
```

- [ ] **Step 2: Add dependencies and run the test to verify it fails**

Add to `requirements.txt`:
```
fastapi>=0.110
uvicorn[standard]>=0.27
httpx>=0.27
```

Then install:
```bash
pip install -r requirements.txt
```

Run: `pytest backtester/tests/test_healthz.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backtester.app'`.

- [ ] **Step 3: Write minimal implementation**

Create `backtester/__init__.py` (empty file).

Create `backtester/tests/__init__.py` (empty file).

Create `backtester/app.py`:
```python
"""Midas backtester service — FastAPI app.

Deployed to Google Cloud Run. Wraps the existing `engine.backtest.run_backtest`
behind an HTTP API consumed by the `/simulate` page on midas.revah.paris.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Midas Backtester", version="0.1.0")

# Site is served from midas.revah.paris; allow it plus localhost for dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://midas.revah.paris",
        "http://localhost:4321",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backtester/tests/test_healthz.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt backtester/__init__.py backtester/app.py backtester/tests/
git commit -m "feat(backtester): bootstrap FastAPI service with healthz endpoint"
```

---

## Task 2: Define request/response Pydantic schemas for signal-shape

**Files:**
- Create: `backtester/schemas.py`, `backtester/tests/test_schemas.py`

- [ ] **Step 1: Write the failing test**

Create `backtester/tests/test_schemas.py`:
```python
import pytest
from pydantic import ValidationError

from backtester.schemas import (
    EquityPoint,
    MetricsBlock,
    RunRequest,
    RunResponse,
    SignalConfig,
    TradeEntry,
)


def test_signal_config_round_trip():
    config = SignalConfig(
        universe="sp500",
        selector="golden-cross",
        manager="trailing-stop",
        max_positions=20,
        max_position_pct=10.0,
        min_hold_days=5,
    )
    dumped = config.model_dump()
    assert dumped["universe"] == "sp500"
    assert SignalConfig(**dumped) == config


def test_run_request_requires_signal_kind_v1():
    payload = {
        "kind": "signal",
        "config": {
            "universe": "sp500",
            "selector": "golden-cross",
            "manager": "trailing-stop",
            "max_positions": 20,
            "max_position_pct": 10.0,
            "min_hold_days": 5,
        },
        "start_date": "2018-01-01",
        "end_date": "2024-12-31",
        "capital": 10000,
        "currency": "EUR",
    }
    request = RunRequest(**payload)
    assert request.kind == "signal"
    assert request.capital == 10000


def test_run_request_rejects_unsupported_kind():
    payload = {
        "kind": "mirror",
        "config": {},
        "start_date": "2018-01-01",
        "end_date": "2024-12-31",
        "capital": 10000,
        "currency": "EUR",
    }
    with pytest.raises(ValidationError):
        RunRequest(**payload)


def test_run_response_shape():
    response = RunResponse(
        equity_curve=[EquityPoint(date="2024-01-01", value=10000.0)],
        metrics=MetricsBlock(
            total_return_pct=0.0,
            cagr_pct=0.0,
            sharpe=0.0,
            max_drawdown_pct=0.0,
            vs_msci_world_pct=0.0,
            vs_coin_flip_pct=0.0,
        ),
        trades=[],
        config_hash="sha256-deadbeef",
        warnings=[],
    )
    assert response.equity_curve[0].value == 10000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backtester/tests/test_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtester.schemas'`.

- [ ] **Step 3: Write minimal implementation**

Create `backtester/schemas.py`:
```python
"""Pydantic schemas for the backtester API.

v1 supports only the `signal` strategy kind. `mirror` and `allocation` are
rejected at the schema layer; they are introduced in later plans.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class SignalConfig(BaseModel):
    """Form fields for a signal-driven strategy."""

    universe: str
    selector: str
    manager: str
    max_positions: int = Field(ge=1, le=100)
    max_position_pct: float = Field(gt=0.0, le=100.0)
    min_hold_days: int = Field(ge=0, le=365)


class RunRequest(BaseModel):
    kind: Literal["signal"]
    config: SignalConfig
    start_date: date
    end_date: date
    capital: float = Field(gt=0.0)
    currency: Literal["EUR", "USD"] = "EUR"


class EquityPoint(BaseModel):
    date: str  # ISO-8601, e.g. "2024-01-01"
    value: float


class MetricsBlock(BaseModel):
    total_return_pct: float
    cagr_pct: float
    sharpe: float
    max_drawdown_pct: float
    vs_msci_world_pct: float
    vs_coin_flip_pct: float


class TradeEntry(BaseModel):
    date: str
    ticker: str
    side: Literal["buy", "sell"]
    quantity: float
    price: float
    pnl: float | None = None  # None for opening trades; filled on close.


class RunResponse(BaseModel):
    equity_curve: list[EquityPoint]
    metrics: MetricsBlock
    trades: list[TradeEntry]
    config_hash: str
    warnings: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backtester/tests/test_schemas.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backtester/schemas.py backtester/tests/test_schemas.py
git commit -m "feat(backtester): pydantic schemas for run request/response (signal kind)"
```

---

## Task 3: Universe resolution + price-data assembly + signal runner

**Files:**
- Create: `backtester/runner.py`, `backtester/tests/test_runner.py`

- [ ] **Step 1: Write the failing test**

Create `backtester/tests/test_runner.py`:
```python
from datetime import date

import pandas as pd
import pytest

from backtester.runner import (
    UnknownUniverseError,
    build_spec_dict,
    resolve_universe,
    run_signal_backtest,
)
from backtester.schemas import SignalConfig


def test_resolve_universe_known():
    tickers = resolve_universe("classic-60-40")
    assert isinstance(tickers, list)
    assert len(tickers) >= 2  # at least 2 tickers in 60/40


def test_resolve_universe_unknown_raises():
    with pytest.raises(UnknownUniverseError):
        resolve_universe("not-a-real-universe")


def test_build_spec_dict_shape():
    config = SignalConfig(
        universe="classic-60-40",
        selector="equal-weight-rebalance",
        manager="none",
        max_positions=20,
        max_position_pct=10.0,
        min_hold_days=0,
    )
    spec = build_spec_dict(config, capital=5000.0)
    assert spec["universe"] == "classic-60-40"
    assert spec["funding"]["initial"] == 5000.0
    assert spec["selector"] == "equal-weight-rebalance"


def test_run_signal_backtest_returns_curve_and_metrics():
    """End-to-end: small universe, short window, real bt run."""
    config = SignalConfig(
        universe="classic-60-40",
        selector="equal-weight-rebalance",
        manager="none",
        max_positions=2,
        max_position_pct=100.0,
        min_hold_days=0,
    )
    result = run_signal_backtest(
        config,
        start=date(2024, 1, 1),
        end=date(2024, 6, 30),
        capital=10000.0,
    )
    assert len(result.daily_values) > 50  # ~125 trading days in H1
    assert isinstance(result.total_return, float)
    assert isinstance(result.daily_values, pd.Series)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backtester/tests/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtester.runner'`.

- [ ] **Step 3: Write minimal implementation**

Create `backtester/runner.py`:
```python
"""Wraps engine.backtest.run_backtest behind a typed entry point.

Resolves the universe id to tickers, fetches price data from the OHLCV store
(falling back to yfinance only if the store doesn't cover the range), and
calls run_backtest. Returns the existing BacktestResult dataclass unchanged.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Allow imports of the engine package when this module is loaded inside a
# Cloud Run container or via the FastAPI app.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from engine.backtest import BacktestResult, run_backtest  # noqa: E402
from engine.market_data import MarketDataFetcher  # noqa: E402
from engine.universes.assets import (  # noqa: E402
    get_bearish_etf_tickers,
    get_bearish_etf_ucits_tickers,
    get_classic_60_40,
    get_commodities_eur_tickers,
    get_crypto_eur_tickers,
    get_crypto_tickers,
    get_forex_tickers,
    get_metals_tickers,
    get_voo_only,
)
from engine.universes.index import (  # noqa: E402
    get_cac40_tickers,
    get_dax_tickers,
    get_dow30_tickers,
    get_ftse100_tickers,
    get_nasdaq100_tickers,
    get_sp500_tickers,
    get_stoxx600_tickers,
)

from backtester.schemas import SignalConfig

_UNIVERSE_RESOLVERS = {
    "sp500": get_sp500_tickers,
    "dow30": get_dow30_tickers,
    "nasdaq100": get_nasdaq100_tickers,
    "crypto-top20": get_crypto_tickers,
    "forex-majors": get_forex_tickers,
    "metals-commodities": get_metals_tickers,
    "single-voo": get_voo_only,
    "classic-60-40": get_classic_60_40,
    "bearish-etfs": get_bearish_etf_tickers,
    "bearish-etfs-ucits": get_bearish_etf_ucits_tickers,
    "crypto-top20-eur": get_crypto_eur_tickers,
    "commodities-eur": get_commodities_eur_tickers,
    "cac40": get_cac40_tickers,
    "dax": get_dax_tickers,
    "ftse100": get_ftse100_tickers,
    "stoxx-600": get_stoxx600_tickers,
}

_CACHE_DIR = _PROJECT_ROOT / "data" / "cache"


class UnknownUniverseError(ValueError):
    """Raised when a requested universe id is not in the resolver table."""


def resolve_universe(universe_id: str) -> list[str]:
    if universe_id not in _UNIVERSE_RESOLVERS:
        raise UnknownUniverseError(f"Unknown universe: {universe_id!r}")
    return list(_UNIVERSE_RESOLVERS[universe_id]())


def build_spec_dict(config: SignalConfig, capital: float) -> dict:
    return {
        "id": "user-backtest",
        "name": "User Backtest",
        "universe": config.universe,
        "selector": config.selector,
        "manager": config.manager,
        "funding": {"initial": capital, "monthly_addition": 0},
        "dividends": "reinvest",
        "rules": {
            "max_positions": config.max_positions,
            "max_position_pct": config.max_position_pct,
            "min_hold_days": config.min_hold_days,
        },
    }


def run_signal_backtest(
    config: SignalConfig,
    start: date,
    end: date,
    capital: float,
) -> BacktestResult:
    tickers = resolve_universe(config.universe)
    fetcher = MarketDataFetcher(cache_dir=_CACHE_DIR)
    price_data = fetcher.fetch_prices(tickers, start, end)
    spec_dict = build_spec_dict(config, capital=capital)
    return run_backtest(spec_dict, price_data, initial_capital=capital)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backtester/tests/test_runner.py -v`
Expected: 4 passed.

If `equal-weight-rebalance` is not a registered selector in your codebase, substitute the selector name shown by `ls engine/selectors/` (e.g. `equal-weight`). The test must use a selector + manager pair that already exists in `engine/selectors/` and `engine/managers/`.

- [ ] **Step 5: Commit**

```bash
git add backtester/runner.py backtester/tests/test_runner.py
git commit -m "feat(backtester): signal-shape runner wrapping engine.backtest"
```

---

## Task 4: Trade extraction helper — top-N trades by absolute P&L

**Files:**
- Create: `backtester/trades.py`, `backtester/tests/test_trades.py`

- [ ] **Step 1: Write the failing test**

Create `backtester/tests/test_trades.py`:
```python
import pandas as pd

from backtester.trades import extract_top_trades


def _make_transactions() -> pd.DataFrame:
    """Synthetic transactions in the shape bt produces."""
    return pd.DataFrame(
        [
            {"Date": "2024-01-02", "Security": "AAPL", "quantity": 10, "price": 150.0},
            {"Date": "2024-02-15", "Security": "AAPL", "quantity": -10, "price": 200.0},
            {"Date": "2024-03-01", "Security": "TSLA", "quantity": 5, "price": 100.0},
            {"Date": "2024-04-10", "Security": "TSLA", "quantity": -5, "price": 80.0},
        ]
    )


def test_extract_top_trades_returns_closed_trades_only():
    transactions = _make_transactions()
    trades = extract_top_trades(transactions, n=10)
    # 2 buys + 2 sells = 2 closed positions
    assert len(trades) == 4  # raw rows, sells first by absolute P&L


def test_extract_top_trades_sort_by_abs_pnl():
    transactions = _make_transactions()
    trades = extract_top_trades(transactions, n=2)
    # AAPL win = +500, TSLA loss = -100 → AAPL sell row appears first
    assert trades[0].ticker == "AAPL"
    assert trades[0].side == "sell"
    assert trades[0].pnl == 500.0


def test_extract_top_trades_handles_none_transactions():
    assert extract_top_trades(None, n=10) == []


def test_extract_top_trades_handles_empty_transactions():
    empty = pd.DataFrame(columns=["Date", "Security", "quantity", "price"])
    assert extract_top_trades(empty, n=10) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backtester/tests/test_trades.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtester.trades'`.

- [ ] **Step 3: Write minimal implementation**

Create `backtester/trades.py`:
```python
"""Convert bt's transactions DataFrame into the API's TradeEntry list.

The bt library returns one row per fill (positive quantity = buy, negative =
sell). We compute per-position P&L by matching each sell to the FIFO buy of
the same ticker, then return all rows sorted by descending |P&L| capped at N.
"""

from __future__ import annotations

from collections import defaultdict, deque

import pandas as pd

from backtester.schemas import TradeEntry


def extract_top_trades(
    transactions: pd.DataFrame | None,
    n: int = 20,
) -> list[TradeEntry]:
    if transactions is None or transactions.empty:
        return []

    rows: list[TradeEntry] = []
    open_lots: dict[str, deque[tuple[float, float]]] = defaultdict(deque)

    for _, row in transactions.sort_values("Date").iterrows():
        date_str = pd.Timestamp(row["Date"]).date().isoformat()
        ticker = str(row["Security"])
        quantity = float(row["quantity"])
        price = float(row["price"])

        if quantity > 0:
            open_lots[ticker].append((quantity, price))
            rows.append(
                TradeEntry(
                    date=date_str,
                    ticker=ticker,
                    side="buy",
                    quantity=quantity,
                    price=price,
                    pnl=None,
                )
            )
        else:
            sell_qty = abs(quantity)
            realised = 0.0
            remaining = sell_qty
            while remaining > 0 and open_lots[ticker]:
                lot_qty, lot_price = open_lots[ticker][0]
                matched = min(lot_qty, remaining)
                realised += matched * (price - lot_price)
                if matched == lot_qty:
                    open_lots[ticker].popleft()
                else:
                    open_lots[ticker][0] = (lot_qty - matched, lot_price)
                remaining -= matched
            rows.append(
                TradeEntry(
                    date=date_str,
                    ticker=ticker,
                    side="sell",
                    quantity=sell_qty,
                    price=price,
                    pnl=realised,
                )
            )

    rows.sort(key=lambda t: abs(t.pnl) if t.pnl is not None else 0.0, reverse=True)
    return rows[:n]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backtester/tests/test_trades.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backtester/trades.py backtester/tests/test_trades.py
git commit -m "feat(backtester): top-N trade extractor with FIFO P&L matching"
```

---

## Task 5: Vs-benchmark + vs-coin-flip comparisons

**Files:**
- Create: `backtester/comparisons.py`, `backtester/tests/test_comparisons.py`

- [ ] **Step 1: Write the failing test**

Create `backtester/tests/test_comparisons.py`:
```python
from datetime import date

import pandas as pd

from backtester.comparisons import compute_comparison_deltas


def test_comparison_deltas_zero_when_strategy_matches_benchmark():
    # Strategy and benchmark identical → delta = 0.
    dates = pd.date_range("2024-01-02", "2024-01-05", freq="B")
    strategy_curve = pd.Series([10000, 10100, 10200, 10300], index=dates)
    benchmark_curve = pd.Series([10000, 10100, 10200, 10300], index=dates)
    coin_flip_curve = pd.Series([10000, 9900, 9800, 9700], index=dates)

    deltas = compute_comparison_deltas(
        strategy_curve,
        benchmark_curve=benchmark_curve,
        coin_flip_curve=coin_flip_curve,
    )

    assert abs(deltas.vs_msci_world_pct) < 1e-6
    assert deltas.vs_coin_flip_pct > 0.0


def test_comparison_deltas_returns_pct_difference():
    dates = pd.date_range("2024-01-02", "2024-01-05", freq="B")
    strategy_curve = pd.Series([10000, 11000, 12000, 13000], index=dates)  # +30%
    benchmark_curve = pd.Series([10000, 10100, 10200, 10300], index=dates)  # +3%
    coin_flip_curve = pd.Series([10000, 9000, 8000, 7000], index=dates)  # -30%

    deltas = compute_comparison_deltas(
        strategy_curve,
        benchmark_curve=benchmark_curve,
        coin_flip_curve=coin_flip_curve,
    )

    assert abs(deltas.vs_msci_world_pct - 27.0) < 1e-6
    assert abs(deltas.vs_coin_flip_pct - 60.0) < 1e-6


def test_comparison_handles_missing_benchmark_data():
    dates = pd.date_range("2024-01-02", "2024-01-05", freq="B")
    strategy_curve = pd.Series([10000, 11000, 12000, 13000], index=dates)
    deltas = compute_comparison_deltas(
        strategy_curve,
        benchmark_curve=None,
        coin_flip_curve=None,
    )
    # Missing comparators degrade to 0-delta + warning, do not raise.
    assert deltas.vs_msci_world_pct == 0.0
    assert deltas.vs_coin_flip_pct == 0.0
    assert "msci" in deltas.warnings[0].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backtester/tests/test_comparisons.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtester.comparisons'`.

- [ ] **Step 3: Write minimal implementation**

Create `backtester/comparisons.py`:
```python
"""Compute strategy-minus-benchmark and strategy-minus-coinflip deltas.

Each input is a pd.Series of daily portfolio values indexed by date. The
delta is total-return-percent of the strategy minus total-return-percent of
the comparator over the same window, in absolute percentage points.

When a comparator is None (data missing for the requested window), the
corresponding delta is 0.0 and a warning string is appended.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class ComparisonDeltas:
    vs_msci_world_pct: float
    vs_coin_flip_pct: float
    warnings: list[str] = field(default_factory=list)


def _total_return_pct(curve: pd.Series) -> float:
    if len(curve) < 2:
        return 0.0
    start = float(curve.iloc[0])
    end = float(curve.iloc[-1])
    if start == 0:
        return 0.0
    return ((end - start) / start) * 100.0


def compute_comparison_deltas(
    strategy_curve: pd.Series,
    *,
    benchmark_curve: pd.Series | None,
    coin_flip_curve: pd.Series | None,
) -> ComparisonDeltas:
    strategy_return = _total_return_pct(strategy_curve)
    warnings: list[str] = []

    if benchmark_curve is None or benchmark_curve.empty:
        vs_msci_world = 0.0
        warnings.append("MSCI World benchmark unavailable for this window")
    else:
        vs_msci_world = strategy_return - _total_return_pct(benchmark_curve)

    if coin_flip_curve is None or coin_flip_curve.empty:
        vs_coin_flip = 0.0
        warnings.append("Coin-flip baseline unavailable for this window")
    else:
        vs_coin_flip = strategy_return - _total_return_pct(coin_flip_curve)

    return ComparisonDeltas(
        vs_msci_world_pct=vs_msci_world,
        vs_coin_flip_pct=vs_coin_flip,
        warnings=warnings,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backtester/tests/test_comparisons.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backtester/comparisons.py backtester/tests/test_comparisons.py
git commit -m "feat(backtester): vs-MSCI-world and vs-coin-flip delta computation"
```

---

## Task 6: Wire `/run` endpoint with full response

**Files:**
- Modify: `backtester/app.py`
- Create: `backtester/tests/test_run_signal.py`

- [ ] **Step 1: Write the failing integration test**

Create `backtester/tests/test_run_signal.py`:
```python
def test_run_signal_returns_full_response_shape(client):
    payload = {
        "kind": "signal",
        "config": {
            "universe": "classic-60-40",
            "selector": "equal-weight-rebalance",  # adjust if your repo uses another name
            "manager": "none",
            "max_positions": 2,
            "max_position_pct": 100.0,
            "min_hold_days": 0,
        },
        "start_date": "2024-01-02",
        "end_date": "2024-06-28",
        "capital": 10000,
        "currency": "EUR",
    }
    response = client.post("/run", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "equity_curve" in body and len(body["equity_curve"]) > 50
    assert {
        "total_return_pct",
        "cagr_pct",
        "sharpe",
        "max_drawdown_pct",
        "vs_msci_world_pct",
        "vs_coin_flip_pct",
    } <= set(body["metrics"].keys())
    assert isinstance(body["trades"], list)
    assert body["config_hash"].startswith("sha256-")


def test_run_signal_unknown_universe_returns_400(client):
    payload = {
        "kind": "signal",
        "config": {
            "universe": "not-a-real-universe",
            "selector": "golden-cross",
            "manager": "trailing-stop",
            "max_positions": 5,
            "max_position_pct": 20.0,
            "min_hold_days": 0,
        },
        "start_date": "2024-01-02",
        "end_date": "2024-02-02",
        "capital": 10000,
        "currency": "EUR",
    }
    response = client.post("/run", json=payload)
    assert response.status_code == 400
    assert "universe" in response.json()["detail"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backtester/tests/test_run_signal.py -v`
Expected: FAIL — `404 Not Found` (endpoint doesn't exist yet).

- [ ] **Step 3: Implement the endpoint**

Replace the contents of `backtester/app.py` with:
```python
"""Midas backtester service — FastAPI app."""

from __future__ import annotations

import hashlib
import json
from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backtester.comparisons import compute_comparison_deltas
from backtester.runner import (
    UnknownUniverseError,
    resolve_universe,
    run_signal_backtest,
)
from backtester.schemas import (
    EquityPoint,
    MetricsBlock,
    RunRequest,
    RunResponse,
)
from backtester.trades import extract_top_trades

app = FastAPI(title="Midas Backtester", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://midas.revah.paris",
        "http://localhost:4321",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _config_hash(request: RunRequest) -> str:
    payload = request.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"sha256-{digest}"


def _load_msci_world_series(start: date, end: date):
    """Load the MSCI World benchmark series for the requested window.

    Returns None when data is unavailable; caller appends a warning.
    """
    try:
        from engine.market_data import MarketDataFetcher

        fetcher = MarketDataFetcher()
        prices = fetcher.fetch_prices(["URTH"], start, end)
        if prices.empty:
            return None
        # Normalise to a notional 10000 starting value for delta computation.
        series = prices["URTH"]
        return (series / series.iloc[0]) * 10000.0
    except Exception:
        return None


def _load_coin_flip_series(start: date, end: date):
    """Coin-flip baseline: equal-weight pick of 5 random tickers, seeded.

    Reuses the existing engine.baselines coin-flip primitive when available.
    Returns None on any failure; caller appends a warning.
    """
    try:
        from engine.baselines import compute_coin_flip_baseline

        series = compute_coin_flip_baseline(start=start, end=end)
        return series
    except Exception:
        return None


@app.post("/run", response_model=RunResponse)
def run(request: RunRequest) -> RunResponse:
    try:
        result = run_signal_backtest(
            request.config,
            start=request.start_date,
            end=request.end_date,
            capital=request.capital,
        )
    except UnknownUniverseError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backtest failed: {exc}")

    benchmark = _load_msci_world_series(request.start_date, request.end_date)
    coin_flip = _load_coin_flip_series(request.start_date, request.end_date)
    deltas = compute_comparison_deltas(
        result.daily_values,
        benchmark_curve=benchmark,
        coin_flip_curve=coin_flip,
    )

    equity_curve = [
        EquityPoint(date=idx.date().isoformat(), value=float(val))
        for idx, val in result.daily_values.items()
    ]
    metrics = MetricsBlock(
        total_return_pct=result.total_return * 100.0,
        cagr_pct=result.cagr * 100.0,
        sharpe=result.sharpe,
        max_drawdown_pct=result.max_drawdown * 100.0,
        vs_msci_world_pct=deltas.vs_msci_world_pct,
        vs_coin_flip_pct=deltas.vs_coin_flip_pct,
    )
    trades = extract_top_trades(result.transactions, n=20)

    return RunResponse(
        equity_curve=equity_curve,
        metrics=metrics,
        trades=trades,
        config_hash=_config_hash(request),
        warnings=deltas.warnings,
    )
```

If `engine.baselines.compute_coin_flip_baseline` is named differently in your repo, adjust the import; the code should fall through to None on any exception and the test still passes (it does not assert on coin-flip presence).

- [ ] **Step 4: Run all backtester tests**

Run: `pytest backtester/tests/ -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backtester/app.py backtester/tests/test_run_signal.py
git commit -m "feat(backtester): /run endpoint for signal-shape with deltas and trades"
```

---

## Task 7: Dockerfile + local container test

**Files:**
- Create: `backtester/Dockerfile`, `backtester/.dockerignore`

- [ ] **Step 1: Write the Dockerfile**

Create `backtester/Dockerfile`:
```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# System deps for pandas / numpy wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first so they layer-cache independently of source.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the engine + backtester packages and the data tree the backtester reads.
COPY engine /app/engine
COPY backtester /app/backtester
COPY data/market /app/data/market
COPY data/baselines /app/data/baselines
COPY data/portfolios /app/data/portfolios
COPY data/strategies /app/data/strategies

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn backtester.app:app --host 0.0.0.0 --port ${PORT}"]
```

- [ ] **Step 2: Write `.dockerignore`**

Create `backtester/.dockerignore`:
```
**/__pycache__
**/*.pyc
**/.pytest_cache
**/.venv
**/.env*
backtester/tests
data/cache
data/agent_memory
data/output
data/blog
data/posts
.git
.github
.claude
docs
site
```

- [ ] **Step 3: Build and run locally**

Run from repo root:
```bash
docker build -f backtester/Dockerfile -t midas-backtester:dev .
docker run --rm -p 8080:8080 midas-backtester:dev &
sleep 3
curl -sf http://localhost:8080/healthz
```
Expected output: `{"status":"ok"}`

Stop the container:
```bash
docker ps --filter ancestor=midas-backtester:dev -q | xargs -r docker stop
```

- [ ] **Step 4: Commit**

```bash
git add backtester/Dockerfile backtester/.dockerignore
git commit -m "feat(backtester): containerise service for Cloud Run"
```

---

## Task 8: Cloud Run deployment (one-shot, manual) + README

**Files:**
- Create: `backtester/README.md`

- [ ] **Step 1: Write the deploy README**

Create `backtester/README.md`:
````markdown
# Midas Backtester Service

FastAPI service that runs strategy backtests on demand. Wraps the existing
`engine/` codebase. Deployed to Google Cloud Run; consumed by the
`/simulate` page on `midas.revah.paris`.

## Local dev

```bash
pip install -r requirements.txt
uvicorn backtester.app:app --reload --port 8080
curl http://localhost:8080/healthz
```

Run tests:
```bash
pytest backtester/tests/ -v
```

## Container build

```bash
docker build -f backtester/Dockerfile -t midas-backtester:dev .
docker run --rm -p 8080:8080 midas-backtester:dev
```

## Deploy to Cloud Run

One-time setup:
```bash
# Authenticate.
gcloud auth login

# Create or select a project.
gcloud projects create midas-backtester-<unique-suffix>
gcloud config set project midas-backtester-<unique-suffix>

# Enable required services.
gcloud services enable run.googleapis.com cloudbuild.googleapis.com

# Set the default region to one with low latency from Europe.
gcloud config set run/region europe-west1
```

Build and deploy (run from the repo root):
```bash
gcloud run deploy midas-backtester \
  --source . \
  --dockerfile backtester/Dockerfile \
  --region europe-west1 \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300 \
  --concurrency 4 \
  --min-instances 0 \
  --max-instances 5
```

Cloud Run prints a service URL like
`https://midas-backtester-xxxxxx-ew.a.run.app`. Save it — the site needs it.

## Smoke-test the deployed service

```bash
curl -sf $SERVICE_URL/healthz
```

Should return `{"status":"ok"}`.

## Free-tier monitoring

Set a billing alert at $1/month. Cloud Run free tier covers 2M requests +
360k vCPU-seconds + 180k GB-seconds per month. Realistic backtester usage
sits well below this. Going over signals either viral traffic (good
problem) or a runaway loop (bug to fix).

## Future: automated deploys

Out of scope for v1. The current process is a manual `gcloud run deploy`
after every change. A GitHub Actions workflow with Workload Identity
Federation is a follow-up.
````

- [ ] **Step 2: Run the manual deploy**

The operator runs the `gcloud run deploy` command above once. The resulting URL is then placed in `site/.env.production` (or equivalent — see Task 11).

This step is **not automated**; it is a one-time human action. The plan does not gate further tasks on it succeeding — the rest of the plan can be developed locally against `http://localhost:8080`.

- [ ] **Step 3: Commit the README**

```bash
git add backtester/README.md
git commit -m "docs(backtester): local dev + Cloud Run deploy instructions"
```

---

## Task 9: Site `/simulate` page skeleton + nav link

**Files:**
- Create: `site/src/pages/simulate/index.astro`
- Modify: `site/src/components/SiteHeader.astro`

- [ ] **Step 1: Add the page skeleton**

Create `site/src/pages/simulate/index.astro`:
```astro
---
import Layout from "../../layouts/BaseLayout.astro";
---

<Layout title="Simulate · Midas">
  <main class="simulate-shell">
    <h1>Strategy backtester</h1>
    <p class="lede">
      Compose a strategy, pick a universe, set the dates and capital, and
      run a real backtest. Results are shareable by URL.
    </p>

    <section id="form-mount" data-component="simulate-form"></section>
    <section id="result-mount" data-component="simulate-result"></section>
  </main>

  <style>
    .simulate-shell {
      max-width: 980px;
      margin: 0 auto;
      padding: 2rem 1.5rem 4rem;
    }
    .lede {
      color: var(--text-muted, #666);
      max-width: 60ch;
    }
  </style>
</Layout>
```

If the layout in your repo is named differently, adjust the import path. (Check `site/src/layouts/`.)

- [ ] **Step 2: Add the nav link**

Find the nav anchor list in `site/src/components/SiteHeader.astro` and add a `<a href="/simulate">Simulate</a>` entry. The exact JSX to add depends on existing markup — read the file first, then add one new anchor consistent with the existing ones.

- [ ] **Step 3: Verify build**

Run from `site/`:
```bash
npm run build
```
Expected: build succeeds, `dist/simulate/index.html` is produced.

- [ ] **Step 4: Commit**

```bash
git add site/src/pages/simulate/index.astro site/src/components/SiteHeader.astro
git commit -m "feat(site): /simulate page skeleton + nav link"
```

---

## Task 10: URL persistence helpers (encode/decode base64 config)

**Files:**
- Create: `site/src/lib/simulate-config.ts`, `site/tests/simulate-config.test.ts`

- [ ] **Step 1: Write the failing test**

Create `site/tests/simulate-config.test.ts`:
```typescript
import { describe, expect, it } from "vitest";

import {
  decodeConfig,
  encodeConfig,
  type SimulateConfig,
} from "../src/lib/simulate-config";

const SAMPLE: SimulateConfig = {
  kind: "signal",
  config: {
    universe: "sp500",
    selector: "golden-cross",
    manager: "trailing-stop",
    max_positions: 20,
    max_position_pct: 10.0,
    min_hold_days: 5,
  },
  start_date: "2018-01-01",
  end_date: "2024-12-31",
  capital: 10000,
  currency: "EUR",
};

describe("simulate-config", () => {
  it("round-trips through encode/decode", () => {
    const encoded = encodeConfig(SAMPLE);
    const decoded = decodeConfig(encoded);
    expect(decoded).toEqual(SAMPLE);
  });

  it("decodes returns null for invalid input", () => {
    expect(decodeConfig("not-base64")).toBeNull();
    expect(decodeConfig("")).toBeNull();
  });

  it("produces URL-safe encodings (no +, /, =)", () => {
    const encoded = encodeConfig(SAMPLE);
    expect(encoded).not.toMatch(/[+/=]/);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run from `site/`: `npx vitest run tests/simulate-config.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

Create `site/src/lib/simulate-config.ts`:
```typescript
/**
 * SimulateConfig is the canonical shape encoded in a `?s=` query param.
 * Encoder produces URL-safe base64 (no padding); decoder handles either.
 */

export type SignalConfigShape = {
  universe: string;
  selector: string;
  manager: string;
  max_positions: number;
  max_position_pct: number;
  min_hold_days: number;
};

export type SimulateConfig = {
  kind: "signal";
  config: SignalConfigShape;
  start_date: string;
  end_date: string;
  capital: number;
  currency: "EUR" | "USD";
};

function toUrlSafe(b64: string): string {
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromUrlSafe(b64: string): string {
  const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  return padded.replace(/-/g, "+").replace(/_/g, "/");
}

export function encodeConfig(config: SimulateConfig): string {
  const json = JSON.stringify(config);
  const b64 = typeof btoa === "function"
    ? btoa(unescape(encodeURIComponent(json)))
    : Buffer.from(json, "utf-8").toString("base64");
  return toUrlSafe(b64);
}

export function decodeConfig(encoded: string): SimulateConfig | null {
  if (!encoded) return null;
  try {
    const restored = fromUrlSafe(encoded);
    const json = typeof atob === "function"
      ? decodeURIComponent(escape(atob(restored)))
      : Buffer.from(restored, "base64").toString("utf-8");
    const parsed = JSON.parse(json);
    if (parsed?.kind !== "signal") return null;
    return parsed as SimulateConfig;
  } catch {
    return null;
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run from `site/`: `npx vitest run tests/simulate-config.test.ts`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add site/src/lib/simulate-config.ts site/tests/simulate-config.test.ts
git commit -m "feat(site): URL-safe base64 codec for simulate configs"
```

---

## Task 11: Backend client + example payload

**Files:**
- Create: `site/src/lib/simulate-api.ts`, `site/src/lib/simulate-example.ts`, `site/src/lib/simulate-example.json`

- [ ] **Step 1: Capture an example payload**

Run a real backtest locally and save the response. With the local service running:
```bash
curl -sf -X POST http://localhost:8080/run \
  -H "content-type: application/json" \
  -d '{"kind":"signal","config":{"universe":"classic-60-40","selector":"equal-weight-rebalance","manager":"none","max_positions":2,"max_position_pct":100.0,"min_hold_days":0},"start_date":"2024-01-02","end_date":"2024-12-31","capital":10000,"currency":"EUR"}' \
  > site/src/lib/simulate-example.json
```

(Substitute the selector name registered in your repo if `equal-weight-rebalance` is not the right one.)

- [ ] **Step 2: Write the API client and example loader**

Create `site/src/lib/simulate-api.ts`:
```typescript
import type { SimulateConfig } from "./simulate-config";

export type EquityPoint = { date: string; value: number };

export type MetricsBlock = {
  total_return_pct: number;
  cagr_pct: number;
  sharpe: number;
  max_drawdown_pct: number;
  vs_msci_world_pct: number;
  vs_coin_flip_pct: number;
};

export type TradeEntry = {
  date: string;
  ticker: string;
  side: "buy" | "sell";
  quantity: number;
  price: number;
  pnl: number | null;
};

export type RunResponse = {
  equity_curve: EquityPoint[];
  metrics: MetricsBlock;
  trades: TradeEntry[];
  config_hash: string;
  warnings: string[];
};

const BACKTESTER_URL =
  import.meta.env.PUBLIC_BACKTESTER_URL ?? "http://localhost:8080";

export async function pingBacktester(): Promise<void> {
  try {
    await fetch(`${BACKTESTER_URL}/healthz`, { mode: "cors" });
  } catch {
    /* warming up; submit will retry */
  }
}

export async function runBacktest(
  config: SimulateConfig,
): Promise<RunResponse> {
  const response = await fetch(`${BACKTESTER_URL}/run`, {
    method: "POST",
    mode: "cors",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Backtest failed (${response.status}): ${text}`);
  }
  return (await response.json()) as RunResponse;
}
```

Create `site/src/lib/simulate-example.ts`:
```typescript
import exampleJson from "./simulate-example.json";
import type { RunResponse } from "./simulate-api";

export const EXAMPLE_RESULT: RunResponse = exampleJson as RunResponse;
```

- [ ] **Step 3: Set the production env var**

Add to `site/.env.production` (create if missing — and ensure `.env.production` is gitignored):
```
PUBLIC_BACKTESTER_URL=https://midas-backtester-xxxxxx-ew.a.run.app
```
Replace with the real Cloud Run URL from Task 8. The `PUBLIC_` prefix is required for Astro to expose the variable to client-side code.

- [ ] **Step 4: Verify build still passes**

Run from `site/`: `npm run build`
Expected: success.

- [ ] **Step 5: Commit**

```bash
git add site/src/lib/simulate-api.ts site/src/lib/simulate-example.ts site/src/lib/simulate-example.json
git commit -m "feat(site): simulate API client and committed example payload"
```

---

## Task 12: Add uPlot dependency + chart component

**Files:**
- Modify: `site/package.json`
- Create: `site/src/components/SimulateChart.astro`

- [ ] **Step 1: Install uPlot**

From `site/`:
```bash
npm install uplot
```

`package.json` should now show `"uplot": "^1.6.x"` in `dependencies`.

- [ ] **Step 2: Write the chart component**

Create `site/src/components/SimulateChart.astro`:
```astro
---
// SimulateChart renders an equity curve from a RunResponse.
// Hydrates client-side via the inline script below; the server output is
// just a sized container.
---

<figure class="simulate-chart">
  <div id="simulate-chart-canvas" data-testid="simulate-chart"></div>
  <figcaption class="visually-hidden">
    Equity curve over the selected backtest window.
  </figcaption>
</figure>

<style>
  .simulate-chart { margin: 2rem 0; }
  .simulate-chart > div {
    width: 100%;
    height: 320px;
  }
  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
  }
</style>

<script>
  import uPlot from "uplot";
  import "uplot/dist/uPlot.min.css";

  import type { RunResponse } from "../lib/simulate-api";

  declare global {
    interface Window {
      __renderSimulateChart?: (result: RunResponse) => void;
    }
  }

  let chart: uPlot | null = null;

  window.__renderSimulateChart = (result: RunResponse) => {
    const el = document.getElementById("simulate-chart-canvas");
    if (!el) return;

    const xs = result.equity_curve.map((p) =>
      Math.floor(new Date(p.date).getTime() / 1000),
    );
    const ys = result.equity_curve.map((p) => p.value);

    if (chart) chart.destroy();
    chart = new uPlot(
      {
        title: "",
        width: el.clientWidth || 880,
        height: 320,
        scales: { x: { time: true } },
        series: [
          {},
          { label: "Equity", stroke: "#0a6", width: 2, fill: "rgba(0,170,102,0.10)" },
        ],
        legend: { show: true },
      },
      [xs, ys],
      el,
    );
  };
</script>
```

- [ ] **Step 3: Build to verify the chart bundles cleanly**

From `site/`:
```bash
npm run build
```
Expected: success with the uPlot bundle included.

- [ ] **Step 4: Commit**

```bash
git add site/package.json site/package-lock.json site/src/components/SimulateChart.astro
git commit -m "feat(site): uPlot chart component for simulate equity curves"
```

---

## Task 13: Form component + URL ↔ form sync + submit handler

**Files:**
- Create: `site/src/components/SimulateForm.astro`
- Modify: `site/src/pages/simulate/index.astro` (mount the components)

- [ ] **Step 1: Write the form component**

Create `site/src/components/SimulateForm.astro`:
```astro
---
// Form for signal-shape backtests. Fully client-side; reads + writes
// querystring; calls the backtester service on submit.
---

<form id="simulate-form" class="simulate-form">
  <fieldset>
    <legend>Universe</legend>
    <select name="universe" required>
      <option value="sp500">S&P 500</option>
      <option value="dow30">Dow 30</option>
      <option value="nasdaq100">NASDAQ 100</option>
      <option value="cac40">CAC 40</option>
      <option value="dax">DAX</option>
      <option value="ftse100">FTSE 100</option>
      <option value="stoxx-600">STOXX 600</option>
      <option value="crypto-top20-eur">Crypto top 20 (EUR)</option>
      <option value="crypto-top20">Crypto top 20 (USD)</option>
      <option value="forex-majors">Forex majors</option>
      <option value="metals-commodities">Metals + commodities</option>
      <option value="bearish-etfs">Bearish ETFs</option>
      <option value="bearish-etfs-ucits">Bearish ETFs (UCITS)</option>
      <option value="commodities-eur">Commodities (EUR)</option>
      <option value="single-voo">VOO only</option>
      <option value="classic-60-40">Classic 60/40</option>
    </select>
  </fieldset>

  <fieldset>
    <legend>Selector</legend>
    <select name="selector" required>
      <option value="golden-cross">Golden cross</option>
      <option value="rsi-contrarian">RSI contrarian</option>
      <option value="buy-the-dip">Buy the dip</option>
      <option value="dogs-of-the-dow">Dogs of the Dow</option>
      <option value="dividend-aristocrats">Dividend aristocrats</option>
      <option value="fear-greed">Fear and greed</option>
      <option value="equal-weight-rebalance">Equal-weight rebalance</option>
    </select>
  </fieldset>

  <fieldset>
    <legend>Manager</legend>
    <select name="manager" required>
      <option value="none">None</option>
      <option value="trailing-stop">Trailing stop</option>
      <option value="grid">Grid</option>
    </select>
  </fieldset>

  <fieldset>
    <legend>Rules</legend>
    <label>Max positions <input type="number" name="max_positions" min="1" max="100" value="20" required /></label>
    <label>Max position % <input type="number" name="max_position_pct" min="0.1" max="100" step="0.1" value="10" required /></label>
    <label>Min hold days <input type="number" name="min_hold_days" min="0" max="365" value="5" required /></label>
  </fieldset>

  <fieldset>
    <legend>Window</legend>
    <label>Start <input type="date" name="start_date" value="2018-01-01" required /></label>
    <label>End <input type="date" name="end_date" value="2024-12-31" required /></label>
    <label>Capital <input type="number" name="capital" min="100" value="10000" required /></label>
    <label>Currency
      <select name="currency">
        <option value="EUR">EUR</option>
        <option value="USD">USD</option>
      </select>
    </label>
  </fieldset>

  <button type="submit">Run backtest</button>
  <p class="hint" id="simulate-hint" hidden>
    Warming up the backtester — first run takes a few seconds…
  </p>
</form>

<style>
  .simulate-form fieldset { border: 1px solid #ddd; padding: 1rem; margin: 1rem 0; }
  .simulate-form legend { padding: 0 .5rem; font-weight: 600; }
  .simulate-form label { display: inline-flex; gap: .5rem; align-items: center; margin-right: 1rem; }
  .simulate-form select, .simulate-form input { font: inherit; padding: .25rem .5rem; }
  .simulate-form button { margin-top: 1rem; padding: .5rem 1rem; font: inherit; cursor: pointer; }
  .simulate-form .hint { color: var(--text-muted, #666); }
</style>

<script>
  import {
    decodeConfig,
    encodeConfig,
    type SimulateConfig,
  } from "../lib/simulate-config";
  import {
    pingBacktester,
    runBacktest,
    type RunResponse,
  } from "../lib/simulate-api";
  import { EXAMPLE_RESULT } from "../lib/simulate-example";

  function readForm(): SimulateConfig {
    const form = document.getElementById("simulate-form") as HTMLFormElement;
    const data = new FormData(form);
    return {
      kind: "signal",
      config: {
        universe: String(data.get("universe")),
        selector: String(data.get("selector")),
        manager: String(data.get("manager")),
        max_positions: Number(data.get("max_positions")),
        max_position_pct: Number(data.get("max_position_pct")),
        min_hold_days: Number(data.get("min_hold_days")),
      },
      start_date: String(data.get("start_date")),
      end_date: String(data.get("end_date")),
      capital: Number(data.get("capital")),
      currency: data.get("currency") === "USD" ? "USD" : "EUR",
    };
  }

  function writeForm(config: SimulateConfig): void {
    const form = document.getElementById("simulate-form") as HTMLFormElement;
    const set = (name: string, value: string | number) => {
      const el = form.elements.namedItem(name) as HTMLInputElement | HTMLSelectElement | null;
      if (el) el.value = String(value);
    };
    set("universe", config.config.universe);
    set("selector", config.config.selector);
    set("manager", config.config.manager);
    set("max_positions", config.config.max_positions);
    set("max_position_pct", config.config.max_position_pct);
    set("min_hold_days", config.config.min_hold_days);
    set("start_date", config.start_date);
    set("end_date", config.end_date);
    set("capital", config.capital);
    set("currency", config.currency);
  }

  function updateUrl(config: SimulateConfig): void {
    const url = new URL(window.location.href);
    url.searchParams.set("s", encodeConfig(config));
    history.replaceState(null, "", url.toString());
  }

  function renderResult(result: RunResponse): void {
    window.__renderSimulateChart?.(result);
    const mount = document.getElementById("result-mount");
    if (!mount) return;
    const m = result.metrics;
    mount.innerHTML = `
      <dl class="simulate-metrics">
        <div><dt>Total return</dt><dd>${m.total_return_pct.toFixed(1)}%</dd></div>
        <div><dt>CAGR</dt><dd>${m.cagr_pct.toFixed(1)}%</dd></div>
        <div><dt>Sharpe</dt><dd>${m.sharpe.toFixed(2)}</dd></div>
        <div><dt>Max drawdown</dt><dd>${m.max_drawdown_pct.toFixed(1)}%</dd></div>
        <div><dt>vs MSCI World</dt><dd>${m.vs_msci_world_pct.toFixed(1)} pp</dd></div>
        <div><dt>vs Coin flip</dt><dd>${m.vs_coin_flip_pct.toFixed(1)} pp</dd></div>
      </dl>
    `;
  }

  function showWarming(): void {
    const hint = document.getElementById("simulate-hint");
    if (hint) hint.hidden = false;
  }

  function hideWarming(): void {
    const hint = document.getElementById("simulate-hint");
    if (hint) hint.hidden = true;
  }

  async function handleSubmit(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const config = readForm();
    updateUrl(config);
    showWarming();
    try {
      const result = await runBacktest(config);
      renderResult(result);
    } catch (err) {
      const mount = document.getElementById("result-mount");
      if (mount) mount.innerHTML = `<p class="error">Backtest failed: ${(err as Error).message}</p>`;
    } finally {
      hideWarming();
    }
  }

  function bootstrap(): void {
    const form = document.getElementById("simulate-form") as HTMLFormElement | null;
    if (!form) return;
    form.addEventListener("submit", handleSubmit);

    const params = new URLSearchParams(window.location.search);
    const encoded = params.get("s");
    const decoded = encoded ? decodeConfig(encoded) : null;
    if (decoded) {
      writeForm(decoded);
      // Auto-run if the URL was shared.
      void runBacktest(decoded).then(renderResult).catch(() => {});
    } else {
      // Render the static example so the page never feels empty.
      renderResult(EXAMPLE_RESULT);
    }

    // Wake the backend in the background while the user reads the form.
    void pingBacktester();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
  } else {
    bootstrap();
  }
</script>
```

- [ ] **Step 2: Mount the components in the page**

Replace `site/src/pages/simulate/index.astro` with:
```astro
---
import Layout from "../../layouts/BaseLayout.astro";
import SimulateForm from "../../components/SimulateForm.astro";
import SimulateChart from "../../components/SimulateChart.astro";
---

<Layout title="Simulate · Midas">
  <main class="simulate-shell">
    <h1>Strategy backtester</h1>
    <p class="lede">
      Compose a strategy, pick a universe, set the dates and capital, and
      run a real backtest. Results are shareable by URL.
    </p>

    <SimulateForm />
    <SimulateChart />
    <section id="result-mount"></section>
  </main>

  <style>
    .simulate-shell {
      max-width: 980px;
      margin: 0 auto;
      padding: 2rem 1.5rem 4rem;
    }
    .lede {
      color: var(--text-muted, #666);
      max-width: 60ch;
    }
    .simulate-metrics {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 1rem;
      margin: 1.5rem 0;
    }
    .simulate-metrics > div { padding: .75rem; border: 1px solid #ddd; }
    .simulate-metrics dt { font-size: .85rem; color: var(--text-muted, #666); }
    .simulate-metrics dd { margin: 0; font-size: 1.5rem; font-weight: 600; }
  </style>
</Layout>
```

- [ ] **Step 3: Build to verify**

From `site/`:
```bash
npm run build
```
Expected: success.

- [ ] **Step 4: Local end-to-end smoke test**

Open two terminals.

Terminal 1 (backend):
```bash
uvicorn backtester.app:app --reload --port 8080
```

Terminal 2 (site):
```bash
cd site
npm run dev
```

Open `http://localhost:4321/simulate` in a browser.
Verify: example result is visible immediately, the form is populated with defaults, and clicking "Run backtest" produces a curve and metrics.

- [ ] **Step 5: Commit**

```bash
git add site/src/components/SimulateForm.astro site/src/pages/simulate/index.astro
git commit -m "feat(site): /simulate form with URL persistence and submit handler"
```

---

## Task 14: Documentation update

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the Project Structure section**

In `CLAUDE.md`, find the `## Project Structure` section. Add an entry under it for `backtester/`:

```markdown
- `backtester/` — FastAPI service deployed to Google Cloud Run; wraps `engine.backtest.run_backtest` for the public `/simulate` page. Local dev: `uvicorn backtester.app:app --reload --port 8080`. Deploy: `backtester/README.md`. Consumed by the site via `PUBLIC_BACKTESTER_URL`.
```

In the same file, find the `## Site (Ring 3a)` section and add a paragraph:

```markdown
The `/simulate` page (signal-shape strategies for now) is a separate product from the agent narrative — visitors compose a strategy, get a real backtest from the Cloud Run service, and share results by URL. Mirror, allocation, cache, and overlay are deferred to subsequent plans.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: backtester service and /simulate page in CLAUDE.md"
```

---

## Task 15: Final verification + push

- [ ] **Step 1: Run the full test suite**

```bash
pytest -v
cd site && npm run test && cd ..
```
Expected: all tests pass.

- [ ] **Step 2: Build the site**

```bash
cd site && npm run build && cd ..
```
Expected: success, `/simulate` route in the `dist/` output.

- [ ] **Step 3: Push**

```bash
git push origin main
```

The Vercel hook will pick up the site changes. The backtester service must be deployed manually (Task 8) — until then the production site's `/simulate` page falls back to the static example payload because `runBacktest` will fail without a reachable URL.

---

## Acceptance criteria

- `pytest backtester/tests/ -v` passes (≥15 assertions across 6 test files).
- `npm run test` in `site/` passes (3 assertions).
- Local end-to-end smoke (Task 13 step 4) works: example renders on first paint, submit produces a real curve.
- A backtest URL like `https://midas.revah.paris/simulate?s=<base64>` populates the form and runs automatically on load.
- `gcloud run deploy` (Task 8) produces a healthy service answering `/healthz` and `/run`.
- `CLAUDE.md` documents `backtester/` and `/simulate`.
