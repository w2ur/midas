"""Snapshot rows disclose positions marked at a close their exchange has passed.

Plan 2026-10-03, subtask 1.4. The 2026-09-30 goldfinger row (session
2026-10-01) valued 4GLD.DE and PPFB.DE at their 09-29 closes while the rest of
`.DE` held 09-30, and published 8994.005482565171, byte-identical to the 09-29
row, with nothing on the row saying why. The fixture below rebuilds that night
from the real closes: a `.DE` bucket that holds 09-30 and the two funds that
do not.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from engine.config import get_config
from engine.portfolio import PortfolioManager
from engine.stale_marks import find_stale_marks, is_stale_mark
from engine.types import Trade

# Real store rows at 1286e4c38^ (the 2026-10-01 session's base).
GOLD_4GLD = {"2026-09-25": 120.86000061035156, "2026-09-28": 116.62000274658203, "2026-09-29": 118.0}
GOLD_PPFB = {"2026-09-25": 72.875, "2026-09-28": 70.31999969482422, "2026-09-29": 71.1449966430664}
BUCKET_DAYS = ["2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29", "2026-09-30"]


def _write(store: Path, ticker: str, closes: dict[str, float]) -> None:
    store.mkdir(parents=True, exist_ok=True)
    (store / f"{ticker}.jsonl").write_text(
        "".join(
            json.dumps({"date": d, "close": c, "adj_close": c}) + "\n"
            for d, c in sorted(closes.items())
        )
    )


def _de_bucket(store: Path, days: list[str], members: int = 6) -> None:
    for i in range(members):
        _write(store, f"M{i}.DE", {d: 100.0 + i for d in days})


def _buy(manager: PortfolioManager, agent: str, ticker: str, shares: float, price: float) -> None:
    manager.apply_trade(
        agent,
        Trade(
            id=f"{agent}-{ticker}",
            timestamp=datetime(2026, 9, 8, 20, 0, 0),
            action="BUY",
            ticker=ticker,
            shares=shares,
            price=price,
            total=shares * price,
            fees=0.0,
            reasoning="test fixture",
        ),
    )


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_a_mark_behind_its_bucket_is_stale(tmp_path: Path) -> None:
    _de_bucket(tmp_path, BUCKET_DAYS)
    _write(tmp_path, "4GLD.DE", GOLD_4GLD)
    assert is_stale_mark("4GLD.DE", date(2026, 9, 29), date(2026, 9, 30), store=tmp_path)


def test_a_bucket_closed_wholesale_is_not_stale(tmp_path: Path) -> None:
    """A holiday: no member holds the row's date, so no mark trails the bucket."""
    _de_bucket(tmp_path, BUCKET_DAYS[:-1])
    _write(tmp_path, "4GLD.DE", GOLD_4GLD)
    assert not is_stale_mark("4GLD.DE", date(2026, 9, 29), date(2026, 9, 30), store=tmp_path)


def test_a_mark_on_the_rows_own_date_is_not_stale(tmp_path: Path) -> None:
    _de_bucket(tmp_path, BUCKET_DAYS)
    assert not is_stale_mark("M0.DE", date(2026, 9, 30), date(2026, 9, 30), store=tmp_path)


def test_a_bucket_too_small_to_judge_falls_back_to_the_date(tmp_path: Path) -> None:
    """Fewer than five live `.F` files: over-disclose rather than abstain."""
    _write(tmp_path, "X.F", {"2026-09-29": 1.0})
    assert is_stale_mark("X.F", date(2026, 9, 29), date(2026, 9, 30), store=tmp_path)


def test_futures_mark_at_the_previous_completed_bar_by_design(tmp_path: Path) -> None:
    """`=F` shares the US bucket but is never on a close run: D-1 in a D row is
    the design, D-2 is stale. Without the special case the US majority holding
    D would flag every futures position every night."""
    for i in range(6):
        _write(tmp_path, f"US{i}", {d: 10.0 for d in BUCKET_DAYS})
    on = date(2026, 9, 30)
    assert not is_stale_mark("GC=F", date(2026, 9, 29), on, store=tmp_path)
    assert is_stale_mark("GC=F", date(2026, 9, 28), on, store=tmp_path)
    # Monday's row: the previous completed bar is Friday's.
    assert not is_stale_mark("GC=F", date(2026, 9, 25), date(2026, 9, 28), store=tmp_path)


def test_find_stale_marks_names_ticker_and_price_date_sorted(tmp_path: Path) -> None:
    _de_bucket(tmp_path, BUCKET_DAYS)
    marks = [
        ("PPFB.DE", date(2026, 9, 29)),
        ("M0.DE", date(2026, 9, 30)),
        ("4GLD.DE", date(2026, 9, 29)),
    ]
    assert find_stale_marks(marks, date(2026, 9, 30), store=tmp_path) == [
        {"ticker": "4GLD.DE", "price_date": "2026-09-29"},
        {"ticker": "PPFB.DE", "price_date": "2026-09-29"},
    ]


# ---------------------------------------------------------------------------
# The session writes it
# ---------------------------------------------------------------------------


def _book() -> str:
    """goldfinger on the live desk; any trading agent on midas-core's demo desk,
    where the summaries (keyed on the roster) would not include goldfinger."""
    roster = get_config().trading_roster
    return "goldfinger" if "goldfinger" in roster else roster[0]


def _goldfinger_night(monkeypatch: pytest.MonkeyPatch) -> PortfolioManager:
    store = get_config().ohlcv_dir
    _de_bucket(store, BUCKET_DAYS)
    _write(store, "4GLD.DE", GOLD_4GLD)
    _write(store, "PPFB.DE", GOLD_PPFB)
    manager = PortfolioManager(base_dir=get_config().portfolios_dir)
    manager.initialize(_book(), initial_capital=10_000.0, currency="EUR")
    _buy(manager, _book(), "4GLD.DE", 21.0, 121.07381003243583)
    _buy(manager, _book(), "PPFB.DE", 36.0, 73.65305667453342)
    monkeypatch.setattr(
        "scripts.daily_session.date",
        type("D", (date,), {"today": staticmethod(lambda: date(2026, 10, 1))}),
    )
    return manager


def test_replay_of_the_2026_10_01_session_discloses_goldfinger(
    midas_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.daily_session import step_update_snapshots

    manager = _goldfinger_night(monkeypatch)
    step_update_snapshots({"date": "2026-09-30", "benchmarks": {}})

    (row,) = [r for r in manager.load_snapshots(_book()) if r["date"] == "2026-09-30"]
    # The published value, to the float: the disclosure moves no number.
    assert row["positions_value"] == pytest.approx(5039.219879150391, abs=1e-9)
    assert row["stale_marks"] == [
        {"ticker": "4GLD.DE", "price_date": "2026-09-29"},
        {"ticker": "PPFB.DE", "price_date": "2026-09-29"},
    ]


def test_a_fresh_row_records_an_empty_list(
    midas_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Checked, none" is an empty list, never an absent key."""
    from scripts.daily_session import step_update_snapshots

    manager = _goldfinger_night(monkeypatch)
    store = get_config().ohlcv_dir
    _write(store, "4GLD.DE", {**GOLD_4GLD, "2026-09-30": 119.0})
    _write(store, "PPFB.DE", {**GOLD_PPFB, "2026-09-30": 71.5})
    step_update_snapshots({"date": "2026-09-30", "benchmarks": {}})

    (row,) = [r for r in manager.load_snapshots(_book()) if r["date"] == "2026-09-30"]
    assert row["stale_marks"] == []


def test_old_rows_are_byte_identical_after_a_new_row(midas_data_root: Path) -> None:
    """Append-only: the field lands on new rows only, and serialising the file
    again does not touch a row written before the field existed."""
    manager = PortfolioManager(base_dir=get_config().portfolios_dir)
    manager.initialize("book", initial_capital=10_000.0, currency="EUR")
    manager.add_snapshot(
        strategy_id="book",
        snapshot_date=date(2026, 9, 29),
        portfolio_value=10_000.0,
        cash=10_000.0,
        positions_value=0.0,
        benchmarks={},
        session_date=date(2026, 9, 29),
    )
    before = manager.load_snapshots("book")[0]
    assert "stale_marks" not in before
    before_bytes = json.dumps(before, sort_keys=True)

    manager.add_snapshot(
        strategy_id="book",
        snapshot_date=date(2026, 9, 30),
        portfolio_value=10_000.0,
        cash=10_000.0,
        positions_value=0.0,
        benchmarks={},
        session_date=date(2026, 10, 1),
        stale_marks=[{"ticker": "X.DE", "price_date": "2026-09-29"}],
    )
    old, new = manager.load_snapshots("book")
    assert json.dumps(old, sort_keys=True) == before_bytes
    assert new["stale_marks"] == [{"ticker": "X.DE", "price_date": "2026-09-29"}]


# ---------------------------------------------------------------------------
# The bundle carries it
# ---------------------------------------------------------------------------


def test_bundle_summary_carries_the_newest_rows_stale_marks(
    midas_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.daily_session import build_portfolio_summaries, step_update_snapshots

    _goldfinger_night(monkeypatch)
    step_update_snapshots({"date": "2026-09-30", "benchmarks": {}})
    summary = build_portfolio_summaries()[_book()]
    assert summary["marked_on"] == "2026-09-30"
    assert [m["ticker"] for m in summary["stale_marks"]] == ["4GLD.DE", "PPFB.DE"]


def test_bundle_summary_claims_nothing_for_a_row_without_the_field(
    midas_data_root: Path,
) -> None:
    from scripts.daily_session import build_portfolio_summaries

    manager = PortfolioManager(base_dir=get_config().portfolios_dir)
    manager.initialize(_book(), initial_capital=10_000.0, currency="EUR")
    manager.add_snapshot(
        strategy_id=_book(),
        snapshot_date=date(2026, 9, 29),
        portfolio_value=10_000.0,
        cash=10_000.0,
        positions_value=0.0,
        benchmarks={},
    )
    summary = build_portfolio_summaries()[_book()]
    assert "stale_marks" not in summary
    assert "marked_on" not in summary


# ---------------------------------------------------------------------------
# A restatement recomputes it
# ---------------------------------------------------------------------------


def _restate_fixture(midas_data_root, stale_marks):
    """goldfinger's 2026-09-30 row, written while the store lacked 4GLD.DE's
    09-30 close; the store now holds it."""
    store = get_config().ohlcv_dir
    _de_bucket(store, BUCKET_DAYS)
    _write(store, "4GLD.DE", {**GOLD_4GLD, "2026-09-30": 121.0})
    manager = PortfolioManager(base_dir=get_config().portfolios_dir)
    manager.initialize("book", initial_capital=10_000.0, currency="EUR")
    _buy(manager, "book", "4GLD.DE", 10, 100.0)
    row = {
        "date": "2026-09-30",
        "portfolio_value": 9_000.0 + 10 * GOLD_4GLD["2026-09-29"],
        "cash": 9_000.0,
        "positions_value": 10 * GOLD_4GLD["2026-09-29"],
        "benchmarks": {},
        "session_date": "2026-09-30",
    }
    if stale_marks is not None:
        row["stale_marks"] = stale_marks
    manager._snapshots_path("book").write_text(json.dumps([row]))  # noqa: SLF001
    return manager


def test_a_restated_row_recomputes_its_stale_marks(midas_data_root, monkeypatch) -> None:
    """Regression (review of feat/stage1-asof-reads, 2026-10-04): restatement
    copied the row and recomputed only its values, so a row restated after the
    missing close landed was valued at 09-30 and still disclosed a 09-29 mark:
    the published number and its own disclosure contradicted each other."""
    import scripts.restate_valuations as rv

    monkeypatch.setattr(rv, "_benchmarks_as_of", lambda d: {})
    manager = _restate_fixture(
        midas_data_root, [{"ticker": "4GLD.DE", "price_date": "2026-09-29"}]
    )

    result = rv.restate_agent("book", manager)

    (row,) = result.new_rows
    assert row["positions_value"] == pytest.approx(1210.0)
    assert row["stale_marks"] == []


def test_a_restated_row_without_the_field_does_not_gain_it(midas_data_root, monkeypatch) -> None:
    # A row from before the check existed stays a row from before the check.
    import scripts.restate_valuations as rv

    monkeypatch.setattr(rv, "_benchmarks_as_of", lambda d: {})
    manager = _restate_fixture(midas_data_root, None)

    (row,) = rv.restate_agent("book", manager).new_rows
    assert "stale_marks" not in row
