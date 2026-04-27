from datetime import date

import pandas as pd

from backtester.comparisons import compute_comparison_deltas


def test_comparison_deltas_zero_when_strategy_matches_benchmark():
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
    strategy_curve = pd.Series([10000, 11000, 12000, 13000], index=dates)
    benchmark_curve = pd.Series([10000, 10100, 10200, 10300], index=dates)
    coin_flip_curve = pd.Series([10000, 9000, 8000, 7000], index=dates)

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
    assert deltas.vs_msci_world_pct == 0.0
    assert deltas.vs_coin_flip_pct == 0.0
    assert "msci" in deltas.warnings[0].lower()
