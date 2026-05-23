import json

import pytest


def test_step_write_current_leaderboard_writes_file(tmp_path, monkeypatch):
    from scripts import daily_session

    leaderboard_dir = tmp_path / "data" / "leaderboard"
    leaderboard_dir.mkdir(parents=True)
    monkeypatch.setattr(daily_session, "_PROJECT_ROOT", tmp_path)

    rows = [{"rank": 1, "agent": "a", "return_pct": 5.0}]
    daily_session.step_write_current_leaderboard(
        rows=rows,
        trigger="session-2026-05-22",
    )

    path = leaderboard_dir / "current.json"
    payload = json.loads(path.read_text())
    assert payload["trigger"] == "session-2026-05-22"
    assert payload["rows"] == rows
    assert payload["updated_at"].endswith("Z")


def test_step_write_current_leaderboard_creates_dir_if_missing(tmp_path, monkeypatch):
    """Idempotency: the step works even if data/leaderboard/ doesn't pre-exist."""
    from scripts import daily_session

    monkeypatch.setattr(daily_session, "_PROJECT_ROOT", tmp_path)
    daily_session.step_write_current_leaderboard(
        rows=[],
        trigger="session-2026-05-22",
    )
    assert (tmp_path / "data" / "leaderboard" / "current.json").exists()
