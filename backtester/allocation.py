"""Run an allocation-shape backtest: fixed weights, periodic rebalance.

Visitors specify a list of (ticker, weight) pairs and a rebalance cadence.
We build a bt strategy directly (no JSON spec) and run it against the
existing OHLCV store, then return the same BacktestResult shape that
engine.backtest.run_backtest produces for signal-shape strategies.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import bt  # noqa: E402
import pandas as pd  # noqa: E402

from engine.backtest import BacktestResult  # noqa: E402
from engine.market_data import MarketDataFetcher  # noqa: E402

from backtester.schemas import AllocationConfig

# No parquet query cache — see the note at the top of backtester/runner.py for
# why (in-memory container FS, min-instances=0, and it was the sole reason
# pyarrow shipped in the image).

_CADENCE_ALGOS = {
    "daily": bt.algos.RunDaily,
    "weekly": bt.algos.RunWeekly,
    "monthly": bt.algos.RunMonthly,
    "quarterly": bt.algos.RunQuarterly,
    "yearly": bt.algos.RunYearly,
}


class AllocationError(ValueError):
    """Raised when an allocation config cannot be backtested."""


def run_allocation_backtest(
    config: AllocationConfig,
    start: date,
    end: date,
    capital: float,
) -> BacktestResult:
    if not config.weights:
        raise AllocationError("Allocation must include at least one ticker")

    total_weight = sum(w.weight for w in config.weights)
    if abs(total_weight - 100.0) > 0.01:
        raise AllocationError(
            f"Allocation weights must sum to 100%, got {total_weight:.2f}%"
        )

    tickers = [w.ticker for w in config.weights]
    fetcher = MarketDataFetcher()  # no parquet cache — see runner.py
    price_data = fetcher.fetch_prices(tickers, start, end)

    if not price_data.empty:
        idx = pd.date_range(price_data.index.min(), price_data.index.max(), freq="D")
        price_data = price_data.reindex(idx).ffill().dropna(axis=1, how="all")

    available = set(price_data.columns) if not price_data.empty else set()
    missing = [t for t in tickers if t not in available]
    if missing:
        raise AllocationError(f"No price data available for: {', '.join(missing)}")

    weight_dict = {w.ticker: w.weight / 100.0 for w in config.weights}

    cadence_factory = _CADENCE_ALGOS.get(config.rebalance_cadence)
    if cadence_factory is None:
        raise AllocationError(
            f"Unsupported rebalance cadence: {config.rebalance_cadence!r}"
        )

    strategy_id = "user-allocation"
    strategy = bt.Strategy(
        strategy_id,
        [
            cadence_factory(),
            bt.algos.SelectThese(list(weight_dict.keys())),
            bt.algos.WeighSpecified(**weight_dict),
            bt.algos.Rebalance(),
        ],
    )
    backtest = bt.Backtest(strategy, price_data, initial_capital=capital)
    result = bt.run(backtest)

    stats = result.stats
    total_return = float(stats.loc["total_return", strategy_id])
    cagr = float(stats.loc["cagr", strategy_id])
    sharpe = float(stats.loc["daily_sharpe", strategy_id])
    max_drawdown = float(stats.loc["max_drawdown", strategy_id])
    daily_values: pd.Series = result.backtests[strategy_id].strategy.values

    try:
        transactions: pd.DataFrame | None = result.get_transactions(strategy_id)
    except Exception:
        transactions = None

    return BacktestResult(
        strategy_id=strategy_id,
        strategy_name="User Allocation",
        total_return=total_return,
        cagr=cagr,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        daily_values=daily_values,
        transactions=transactions,
    )
