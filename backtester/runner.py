"""Wraps engine.backtest.run_backtest behind a typed entry point."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

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

from backtester.schemas import SignalConfig  # noqa: E402

_UNIVERSE_RESOLVERS: dict[str, object] = {
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
    """Return the list of tickers for the given universe id.

    Raises UnknownUniverseError if the id is not registered.
    """
    if universe_id not in _UNIVERSE_RESOLVERS:
        raise UnknownUniverseError(f"Unknown universe: {universe_id!r}")
    return list(_UNIVERSE_RESOLVERS[universe_id]())


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
    spec_dict = build_spec_dict(config, capital=capital)
    return run_backtest(spec_dict, price_data, initial_capital=capital)
