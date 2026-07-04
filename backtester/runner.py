"""Wraps engine.backtest.run_backtest behind a typed entry point."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd  # noqa: E402

from engine.backtest import BacktestResult, run_backtest  # noqa: E402
from engine.market_data import MarketDataFetcher  # noqa: E402
from engine.universes import resolve_universe as _engine_resolve_universe  # noqa: E402

from backtester.schemas import SignalConfig  # noqa: E402

_CACHE_DIR = _PROJECT_ROOT / "data" / "cache"


class UnknownUniverseError(ValueError):
    """Raised when a requested universe id is not resolvable (unknown or an
    unimplemented placeholder)."""


def resolve_universe(universe_id: str) -> list[str]:
    """Return the list of tickers for the given universe id.

    Delegates to the single engine registry (engine.universes.resolve_universe)
    and translates its KeyError into UnknownUniverseError so the API layer can
    map it to an HTTP 400 (see backtester.app).
    """
    try:
        return _engine_resolve_universe(universe_id)
    except KeyError as exc:
        raise UnknownUniverseError(str(exc)) from exc


def build_spec_dict(config: SignalConfig, capital: float) -> dict:
    """Build a strategy spec dict from a SignalConfig and capital amount."""
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
    """Run a signal-driven backtest and return the result.

    Resolves the universe, loads price data from the OHLCV store (with
    yfinance fallback), builds the spec dict, and delegates to
    engine.backtest.run_backtest.
    """
    tickers = resolve_universe(config.universe)
    fetcher = MarketDataFetcher(cache_dir=_CACHE_DIR)
    price_data = fetcher.fetch_prices(tickers, start, end)
    # Multi-market universes mix exchanges with different holiday calendars
    # (e.g. CON.DE has no row on 2018-05-01 / German Labour Day, but US
    # tickers do). bt requires a price for every position every day, so we
    # forward-fill across the full daily index — same pattern as
    # engine.baselines._load_price_frame.
    if not price_data.empty:
        idx = pd.date_range(price_data.index.min(), price_data.index.max(), freq="D")
        price_data = price_data.reindex(idx).ffill()
        # Drop tickers whose first known close is still NaN after ffill —
        # they had no price anywhere in the window (e.g. a crypto whose
        # series started after `end`). Otherwise bt fails the same way.
        price_data = price_data.dropna(axis=1, how="all")
    spec_dict = build_spec_dict(config, capital=capital)
    return run_backtest(spec_dict, price_data, initial_capital=capital)
