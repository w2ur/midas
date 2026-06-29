"""Tests for scripts.backfill_snapshots — historical NaN repair + dedupe."""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.config import get_config
from scripts import backfill_snapshots


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data))


@pytest.fixture
def portfolio_root(midas_data_root: Path) -> Path:
    portfolios = get_config().portfolios_dir
    portfolios.mkdir(parents=True, exist_ok=True)
    return portfolios


@pytest.fixture
def fake_ohlcv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stub `latest_close_on_or_before` so tests don't depend on the real store."""
    prices = {
        ("AAPL", date(2026, 4, 17)): 260.0,
        ("AAPL", date(2026, 4, 18)): 270.0,
        ("ASML.AS", date(2026, 4, 17)): 1200.0,
        # ASML.AS deliberately missing for 2026-04-18 — exercises store-miss path.
    }

    def _fake(ticker: str, on: date | None = None, store: Path | None = None):
        if on is None:
            return None
        for (t, d), p in sorted(prices.items(), key=lambda kv: kv[0][1], reverse=True):
            if t == ticker and d <= on:
                return p
        return None

    monkeypatch.setattr(backfill_snapshots, "latest_close_on_or_before", _fake)
    return tmp_path


def _seed_portfolio(
    root: Path, sid: str, *, snapshots: list[dict], trades: list[dict]
) -> Path:
    pdir = root / sid
    pdir.mkdir()
    _write(pdir / "snapshots.json", snapshots)
    _write(pdir / "trades.json", trades)
    return pdir


def test_repairs_nan_via_trade_replay(portfolio_root: Path, fake_ohlcv: Path) -> None:
    pdir = _seed_portfolio(
        portfolio_root,
        "test",
        snapshots=[
            {
                "date": "2026-04-18",
                "portfolio_value": float("nan"),
                "cash": 100.0,
                "positions_value": float("nan"),
                "benchmarks": {"sp500": 5000.0},
            }
        ],
        trades=[
            {
                "id": "t1",
                "timestamp": "2026-04-17T20:00:00+00:00",
                "action": "BUY",
                "ticker": "AAPL",
                "shares": 5.0,
                "price": 260.0,
                "total": 1300.0,
                "fees": 0.0,
            }
        ],
    )

    backfill_snapshots.backfill_portfolio("test")

    snaps = json.loads((pdir / "snapshots.json").read_text())
    assert len(snaps) == 1
    # 5 shares * 270 (the 2026-04-18 close) + 100 cash
    assert snaps[0]["portfolio_value"] == pytest.approx(5 * 270.0 + 100.0)
    assert snaps[0]["positions_value"] == pytest.approx(5 * 270.0)


def test_dedupes_keeping_valid_over_nan(portfolio_root: Path, fake_ohlcv: Path) -> None:
    pdir = _seed_portfolio(
        portfolio_root,
        "test",
        snapshots=[
            {
                "date": "2026-04-18",
                "portfolio_value": float("nan"),
                "cash": 100.0,
                "positions_value": float("nan"),
                "benchmarks": {},
            },
            {
                "date": "2026-04-18",
                "portfolio_value": 9999.0,
                "cash": 100.0,
                "positions_value": 9899.0,
                "benchmarks": {},
            },
        ],
        trades=[],
    )

    backfill_snapshots.backfill_portfolio("test")

    snaps = json.loads((pdir / "snapshots.json").read_text())
    assert len(snaps) == 1
    assert snaps[0]["portfolio_value"] == pytest.approx(9999.0)


def test_falls_back_to_last_trade_price_when_store_silent(
    portfolio_root: Path, fake_ohlcv: Path
) -> None:
    """ASML.AS has no store row for 2026-04-18 → the helper carries forward
    the most recent trade price (1232.0)."""
    pdir = _seed_portfolio(
        portfolio_root,
        "test",
        snapshots=[
            {
                "date": "2026-04-18",
                "portfolio_value": float("nan"),
                "cash": 0.0,
                "positions_value": float("nan"),
                "benchmarks": {},
            }
        ],
        trades=[
            {
                "id": "t1",
                "timestamp": "2026-04-17T20:00:00+00:00",
                "action": "BUY",
                "ticker": "ASML.AS",
                "shares": 2.0,
                "price": 1232.0,
                "total": 2464.0,
                "fees": 0.0,
            }
        ],
    )
    # `fake_ohlcv` returns 1200.0 for ASML.AS on/before 2026-04-17 only.
    backfill_snapshots.backfill_portfolio("test")

    snaps = json.loads((pdir / "snapshots.json").read_text())
    # Store finds 1200.0 (the 2026-04-17 close) — 2 * 1200 = 2400.
    assert snaps[0]["portfolio_value"] == pytest.approx(2400.0)


def test_idempotent_no_changes_on_clean_portfolio(
    portfolio_root: Path, fake_ohlcv: Path
) -> None:
    valid_snapshots = [
        {
            "date": "2026-04-17",
            "portfolio_value": 10000.0,
            "cash": 8700.0,
            "positions_value": 1300.0,
            "benchmarks": {},
        },
        {
            "date": "2026-04-18",
            "portfolio_value": 10050.0,
            "cash": 8700.0,
            "positions_value": 1350.0,
            "benchmarks": {},
        },
    ]
    pdir = _seed_portfolio(portfolio_root, "test", snapshots=valid_snapshots, trades=[])

    _, deduped, repaired = backfill_snapshots.backfill_portfolio("test")

    assert deduped == 0
    assert repaired == 0
    assert json.loads((pdir / "snapshots.json").read_text()) == valid_snapshots


def test_sell_reduces_replayed_position(portfolio_root: Path, fake_ohlcv: Path) -> None:
    pdir = _seed_portfolio(
        portfolio_root,
        "test",
        snapshots=[
            {
                "date": "2026-04-18",
                "portfolio_value": float("nan"),
                "cash": 0.0,
                "positions_value": float("nan"),
                "benchmarks": {},
            }
        ],
        trades=[
            {
                "id": "t1",
                "timestamp": "2026-04-17T20:00:00+00:00",
                "action": "BUY",
                "ticker": "AAPL",
                "shares": 10.0,
                "price": 260.0,
                "total": 2600.0,
                "fees": 0.0,
            },
            {
                "id": "t2",
                "timestamp": "2026-04-18T20:00:00+00:00",
                "action": "SELL",
                "ticker": "AAPL",
                "shares": 4.0,
                "price": 270.0,
                "total": 1080.0,
                "fees": 0.0,
            },
        ],
    )

    backfill_snapshots.backfill_portfolio("test")

    snaps = json.loads((pdir / "snapshots.json").read_text())
    # 6 remaining shares * 270 (2026-04-18 close)
    assert snaps[0]["portfolio_value"] == pytest.approx(6 * 270.0)
