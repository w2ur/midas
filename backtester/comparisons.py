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
