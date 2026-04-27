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
    assert len(tickers) >= 2


def test_resolve_universe_unknown_raises():
    with pytest.raises(UnknownUniverseError):
        resolve_universe("not-a-real-universe")


def test_build_spec_dict_shape():
    config = SignalConfig(
        universe="classic-60-40",
        selector="buy-and-hold",
        manager="fixed-60-40",
        max_positions=20,
        max_position_pct=10.0,
        min_hold_days=0,
    )
    spec = build_spec_dict(config, capital=5000.0)
    assert spec["universe"] == "classic-60-40"
    assert spec["funding"]["initial"] == 5000.0
    assert spec["selector"] == "buy-and-hold"


def test_run_signal_backtest_returns_curve_and_metrics():
    config = SignalConfig(
        universe="classic-60-40",
        selector="buy-and-hold",
        manager="fixed-60-40",
        max_positions=2,
        max_position_pct=100.0,
        min_hold_days=0,
    )
    result = run_signal_backtest(
        config,
        start=date(2024, 5, 1),
        end=date(2024, 10, 31),
        capital=10000.0,
    )
    assert len(result.daily_values) > 50
    assert isinstance(result.total_return, float)
    assert isinstance(result.daily_values, pd.Series)
