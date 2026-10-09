"""Regression tests for #89: a refresh's controls end at its market date.

A refresh run on Monday 2026-10-05 before the US close wrote every control row
dated 10-05 at Friday's closes (``to_date=date.today()``). Those rows are
immutable, so they were compared against books the evening session prices at
Monday's close — and that session's Step 9 found nothing to append and aborted.

The session itself keeps the wall-clock day: see ``step_build_baselines``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import scripts.daily_session as ds
from scripts import refresh_leaderboard


class _Monday(date):
    """`date` whose today() is Monday 2026-10-05, the aborted session's day."""

    @classmethod
    def today(cls) -> date:
        return date(2026, 10, 5)


@pytest.fixture
def captured(midas_data_root: Path, monkeypatch: pytest.MonkeyPatch) -> list[date]:
    import engine.baselines
    import scripts.backfill_baselines as bb

    calls: list[date] = []
    monkeypatch.setattr(ds, "date", _Monday)
    monkeypatch.setattr(ds, "_is_done", lambda _name: False)
    monkeypatch.setattr(ds, "_mark_done", lambda _name: None)
    monkeypatch.setattr(bb, "_universes_by_agent", lambda: {})
    monkeypatch.setattr(bb, "_max_positions_by_agent", lambda: {})
    monkeypatch.setattr(
        engine.baselines,
        "build_all_baselines",
        lambda **kw: calls.append(kw["to_date"]),
    )
    return calls


def test_monday_refresh_before_us_close_writes_no_monday_controls(
    captured: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the refresh: the store's newest bar is Sunday's
    crypto, so the snapshots are keyed 10-04 and the controls end there."""
    monkeypatch.setattr(
        refresh_leaderboard,
        "_step_fetch_market_data",
        lambda: {"date": "2026-10-04", "benchmarks": {}},
    )
    monkeypatch.setattr(refresh_leaderboard, "_step_update_snapshots", lambda p, writer=None: [])
    monkeypatch.setattr(refresh_leaderboard, "_step_build_tax_shadow", lambda: None)
    monkeypatch.setattr(refresh_leaderboard, "_build_portfolio_summaries", dict)
    monkeypatch.setattr(
        refresh_leaderboard, "_build_leaderboard_rows", lambda summaries, on: []
    )
    refresh_leaderboard.run(trigger="crypto-close", today=date(2026, 10, 5))
    assert captured == [date(2026, 10, 4)]


def test_explicit_market_date_caps_the_window(captured: list[date]) -> None:
    ds.step_build_baselines(date(2026, 10, 2))
    assert captured == [date(2026, 10, 2)]


def test_window_never_ends_after_the_wall_clock(captured: list[date]) -> None:
    ds.step_build_baselines(date(2026, 10, 6))
    assert captured == [date(2026, 10, 5)]


def test_session_default_keeps_the_wall_clock_day(captured: list[date]) -> None:
    """The session calls with no argument. On a weekday whose market date did
    not advance its snapshots are refused, and today's control row is what
    keeps the prompt's Step 9 self-check from aborting the whole session."""
    ds.step_build_baselines()
    assert captured == [date(2026, 10, 5)]
