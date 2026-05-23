from datetime import date, datetime, timezone

from engine.leaderboard import (
    build_current_leaderboard_artifact,
    build_leaderboard_rows,
)


def test_build_leaderboard_rows_sorts_by_eur_mtm_descending(monkeypatch):
    from engine import leaderboard as lb

    monkeypatch.setattr(
        lb,
        "portfolio_mtm_eur",
        lambda summary, on: {"a": 12000.0, "b": 9000.0, "c": 11000.0}[
            summary["agent_id"]
        ],
    )
    summaries = {
        "a": {"agent_id": "a"},
        "b": {"agent_id": "b"},
        "c": {"agent_id": "c"},
    }
    rows = build_leaderboard_rows(summaries, on=date(2026, 5, 23))
    assert [r["agent"] for r in rows] == ["a", "c", "b"]
    assert rows[0] == {"rank": 1, "agent": "a", "return_pct": 20.0}
    assert rows[1]["rank"] == 2
    assert rows[2]["return_pct"] == -10.0


def test_build_leaderboard_rows_skips_agents_with_none_mtm(monkeypatch):
    from engine import leaderboard as lb

    monkeypatch.setattr(
        lb,
        "portfolio_mtm_eur",
        lambda summary, on: {"a": 10500.0, "b": None}[summary["agent_id"]],
    )
    summaries = {"a": {"agent_id": "a"}, "b": {"agent_id": "b"}}
    rows = build_leaderboard_rows(summaries, on=date(2026, 5, 23))
    assert [r["agent"] for r in rows] == ["a"]


def test_build_current_leaderboard_artifact_shape(monkeypatch):
    from engine import leaderboard as lb

    monkeypatch.setattr(lb, "portfolio_mtm_eur", lambda summary, on: 10500.0)
    summaries = {"a": {"agent_id": "a"}}
    fixed_now = datetime(2026, 5, 23, 20, 0, 0, tzinfo=timezone.utc)
    artifact = build_current_leaderboard_artifact(
        summaries,
        on=date(2026, 5, 23),
        trigger="scheduled-weekend-refresh",
        updated_at=fixed_now,
    )
    assert artifact == {
        "updated_at": "2026-05-23T20:00:00Z",
        "trigger": "scheduled-weekend-refresh",
        "rows": [{"rank": 1, "agent": "a", "return_pct": 5.0}],
    }
