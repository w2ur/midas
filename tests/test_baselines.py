from datetime import date
from pathlib import Path

import pytest

from engine.baselines import (
    INITIAL,
    BenchmarkSpec,
    compute_passive_benchmark,
)


@pytest.fixture
def tmp_ohlcv(tmp_path, monkeypatch):
    """Redirect the OHLCV store to a temp dir with controllable contents."""
    ohlcv = tmp_path / "market" / "ohlcv"
    ohlcv.mkdir(parents=True)
    monkeypatch.setattr("engine.baselines.OHLCV_DIR", ohlcv)
    return ohlcv


def _write_ohlcv(ohlcv_dir: Path, ticker: str, rows: list[tuple[str, float]]):
    lines = [f'{{"date":"{d}","close":{c}}}' for d, c in rows]
    (ohlcv_dir / f"{ticker}.jsonl").write_text("\n".join(lines) + "\n")


def test_passive_benchmark_starts_at_ten_thousand(tmp_ohlcv):
    _write_ohlcv(
        tmp_ohlcv,
        "TEST",
        [
            ("2026-04-17", 100.0),
            ("2026-04-18", 110.0),
            ("2026-04-21", 99.0),
        ],
    )
    spec = BenchmarkSpec("Test", "TEST", "EUR")
    snaps = compute_passive_benchmark(spec, date(2026, 4, 17), date(2026, 4, 21))
    assert snaps[0]["portfolio_value"] == pytest.approx(INITIAL)
    assert snaps[0]["date"] == "2026-04-17"


def test_passive_benchmark_tracks_price_ratio(tmp_ohlcv):
    _write_ohlcv(
        tmp_ohlcv,
        "TEST",
        [
            ("2026-04-17", 100.0),
            ("2026-04-18", 110.0),
            ("2026-04-21", 80.0),
        ],
    )
    spec = BenchmarkSpec("Test", "TEST", "EUR")
    snaps = compute_passive_benchmark(spec, date(2026, 4, 17), date(2026, 4, 21))
    values = {s["date"]: s["portfolio_value"] for s in snaps}
    assert values["2026-04-18"] == pytest.approx(11000.0)
    assert values["2026-04-21"] == pytest.approx(8000.0)


def test_passive_benchmark_carries_weekend_close(tmp_ohlcv):
    _write_ohlcv(
        tmp_ohlcv,
        "TEST",
        [
            ("2026-04-17", 100.0),  # Friday
            ("2026-04-20", 105.0),  # Monday
        ],
    )
    spec = BenchmarkSpec("Test", "TEST", "EUR")
    snaps = compute_passive_benchmark(spec, date(2026, 4, 17), date(2026, 4, 20))
    values = {s["date"]: s["portfolio_value"] for s in snaps}
    # Saturday + Sunday must exist and carry Friday's value
    assert values["2026-04-18"] == pytest.approx(INITIAL)
    assert values["2026-04-19"] == pytest.approx(INITIAL)
    assert values["2026-04-20"] == pytest.approx(10500.0)


def test_passive_benchmark_flat_cash_sentinel(tmp_ohlcv):
    spec = BenchmarkSpec("EUR cash", "EUR_CASH_FLAT", "EUR")
    snaps = compute_passive_benchmark(spec, date(2026, 4, 17), date(2026, 4, 20))
    assert len(snaps) == 4  # inclusive
    assert all(s["portfolio_value"] == pytest.approx(INITIAL) for s in snaps)


def test_passive_benchmark_includes_currency(tmp_ohlcv):
    _write_ohlcv(tmp_ohlcv, "TEST", [("2026-04-17", 100.0)])
    spec = BenchmarkSpec("Test", "TEST", "USD")
    snaps = compute_passive_benchmark(spec, date(2026, 4, 17), date(2026, 4, 17))
    assert snaps[0]["currency"] == "USD"
