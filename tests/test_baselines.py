from datetime import date
from pathlib import Path

import pytest

from engine.baselines import (
    INITIAL,
    BenchmarkSpec,
    compute_passive_benchmark,
    compute_coin_flip,
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
    assert len(snaps) == 4
    assert all(s["portfolio_value"] == pytest.approx(INITIAL) for s in snaps)
    # Shape contract: every snapshot carries the same five fields so the
    # site can consume them with one loader.
    assert snaps[0].keys() == {
        "date",
        "portfolio_value",
        "cash",
        "positions_value",
        "currency",
    }


def test_passive_benchmark_includes_currency(tmp_ohlcv):
    _write_ohlcv(tmp_ohlcv, "TEST", [("2026-04-17", 100.0)])
    spec = BenchmarkSpec("Test", "TEST", "USD")
    snaps = compute_passive_benchmark(spec, date(2026, 4, 17), date(2026, 4, 17))
    assert snaps[0]["currency"] == "USD"


def test_coin_flip_deterministic_same_seed(tmp_ohlcv):
    for t, rows in [
        ("A", [("2026-04-17", 10.0), ("2026-04-18", 11.0), ("2026-04-21", 12.0)]),
        ("B", [("2026-04-17", 20.0), ("2026-04-18", 19.0), ("2026-04-21", 18.0)]),
        ("C", [("2026-04-17", 50.0), ("2026-04-18", 52.0), ("2026-04-21", 49.0)]),
    ]:
        _write_ohlcv(tmp_ohlcv, t, rows)

    a = compute_coin_flip(
        "test-agent", ["A", "B", "C"], "EUR", 2, date(2026, 4, 17), date(2026, 4, 21)
    )
    b = compute_coin_flip(
        "test-agent", ["A", "B", "C"], "EUR", 2, date(2026, 4, 17), date(2026, 4, 21)
    )
    assert [s["portfolio_value"] for s in a] == [s["portfolio_value"] for s in b]


def test_coin_flip_different_agents_diverge(tmp_ohlcv):
    # 5 tickers with distinct price paths; picking 2 from 5 gives enough
    # outcome space that the two seeds deterministically land on different pairs.
    for t, rows in [
        ("A", [("2026-04-17", 10.0), ("2026-04-18", 12.0), ("2026-04-21", 8.0)]),
        ("B", [("2026-04-17", 20.0), ("2026-04-18", 18.0), ("2026-04-21", 25.0)]),
        ("C", [("2026-04-17", 50.0), ("2026-04-18", 55.0), ("2026-04-21", 40.0)]),
        ("D", [("2026-04-17", 30.0), ("2026-04-18", 28.0), ("2026-04-21", 35.0)]),
        ("E", [("2026-04-17", 15.0), ("2026-04-18", 20.0), ("2026-04-21", 12.0)]),
    ]:
        _write_ohlcv(tmp_ohlcv, t, rows)

    tickers = ["A", "B", "C", "D", "E"]
    a = compute_coin_flip(
        "agent-alpha", tickers, "EUR", 2, date(2026, 4, 17), date(2026, 4, 21)
    )
    b = compute_coin_flip(
        "agent-beta", tickers, "EUR", 2, date(2026, 4, 17), date(2026, 4, 21)
    )
    assert any(ax["portfolio_value"] != bx["portfolio_value"] for ax, bx in zip(a, b))


def test_coin_flip_starts_at_ten_thousand(tmp_ohlcv):
    _write_ohlcv(tmp_ohlcv, "A", [("2026-04-17", 10.0)])
    snaps = compute_coin_flip(
        "x", ["A"], "EUR", 1, date(2026, 4, 17), date(2026, 4, 17)
    )
    assert snaps[0]["portfolio_value"] == pytest.approx(INITIAL)
    assert snaps[0]["currency"] == "EUR"


def test_global_reference_uses_msci_world(tmp_ohlcv):
    from engine.baselines import GLOBAL_REFERENCE, compute_global_reference

    _write_ohlcv(
        tmp_ohlcv,
        GLOBAL_REFERENCE.ticker,
        [
            ("2026-04-17", 100.0),
            ("2026-04-18", 102.0),
        ],
    )
    snaps = compute_global_reference(date(2026, 4, 17), date(2026, 4, 18))
    assert snaps[0]["portfolio_value"] == pytest.approx(INITIAL)
    assert snaps[-1]["portfolio_value"] == pytest.approx(INITIAL * 1.02)
    assert snaps[0]["currency"] == "EUR"


def test_build_all_baselines_writes_files(tmp_ohlcv, tmp_path, monkeypatch):
    """build_all_baselines should produce per-agent + global JSON files."""
    from engine.baselines import build_all_baselines, AGENT_BENCHMARKS, GLOBAL_REFERENCE

    baselines_dir = tmp_path / "baselines"
    monkeypatch.setattr("engine.baselines.BASELINES_DIR", baselines_dir)

    # Minimal OHLCV for every referenced ticker
    for t in {s.ticker for s in AGENT_BENCHMARKS.values()} | {GLOBAL_REFERENCE.ticker}:
        if t == "EUR_CASH_FLAT":
            continue
        _write_ohlcv(tmp_ohlcv, t, [("2026-04-17", 100.0), ("2026-04-18", 105.0)])

    universes_by_agent = {
        agent_id: ["FAKE-A", "FAKE-B"] for agent_id in AGENT_BENCHMARKS
    }
    _write_ohlcv(tmp_ohlcv, "FAKE-A", [("2026-04-17", 10.0), ("2026-04-18", 12.0)])
    _write_ohlcv(tmp_ohlcv, "FAKE-B", [("2026-04-17", 20.0), ("2026-04-18", 19.0)])

    build_all_baselines(
        universes_by_agent=universes_by_agent,
        from_date=date(2026, 4, 17),
        to_date=date(2026, 4, 18),
    )

    for agent_id in AGENT_BENCHMARKS:
        assert (baselines_dir / agent_id / "benchmark.json").exists()
        assert (baselines_dir / agent_id / "coinflip.json").exists()
    assert (baselines_dir / "global" / "msci_world.json").exists()
