import json
from datetime import date

import pytest


def test_watcher_refreshes_current_json_after_fire(tmp_path, monkeypatch):
    from scripts import check_triggers

    (tmp_path / "data" / "leaderboard").mkdir(parents=True)
    monkeypatch.setattr(check_triggers, "_PROJECT_ROOT", tmp_path)

    monkeypatch.setattr(
        check_triggers,
        "_build_portfolio_summaries",
        lambda: {"satoshi": {"agent_id": "satoshi"}},
    )
    monkeypatch.setattr(
        check_triggers,
        "_build_leaderboard_rows",
        lambda summaries, on: [{"rank": 1, "agent": "satoshi", "return_pct": 5.0}],
    )

    check_triggers.refresh_leaderboard_artifact(
        trigger="trigger-fire", on=date(2026, 5, 23)
    )

    payload = json.loads(
        (tmp_path / "data" / "leaderboard" / "current.json").read_text()
    )
    assert payload["trigger"] == "trigger-fire"
    assert payload["rows"][0]["agent"] == "satoshi"
    assert payload["updated_at"].endswith("Z")


def test_watcher_leaderboard_refresh_swallows_errors(tmp_path, monkeypatch, caplog):
    """Critical contract: a leaderboard refresh failure must never raise."""
    import logging

    from scripts import check_triggers

    monkeypatch.setattr(check_triggers, "_PROJECT_ROOT", tmp_path)

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(check_triggers, "_build_portfolio_summaries", _boom)

    with caplog.at_level(logging.WARNING):
        check_triggers.refresh_leaderboard_artifact(
            trigger="trigger-fire", on=date(2026, 5, 23)
        )

    assert any(
        "leaderboard refresh failed" in r.message.lower() for r in caplog.records
    )
