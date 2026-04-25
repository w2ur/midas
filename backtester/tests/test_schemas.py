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
