import json
from datetime import date
from pathlib import Path

import pytest

# Resolve project root so we can probe the committed tax_shadow dir.
_REAL_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _FakeCompleted:
    def __init__(self, returncode):
        self.returncode = returncode


def _make_fake_run(calls, push_returncodes):
    """Build a subprocess.run double that records calls and serves canned
    return codes for `git push`, succeeding for every other command."""
    pushes = iter(push_returncodes)

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["git", "push"]:
            return _FakeCompleted(next(pushes))
        return _FakeCompleted(0)

    return fake_run


def test_push_retries_on_rejection_then_succeeds(monkeypatch):
    """A non-fast-forward rejection triggers a rebase + retry (the weekend
    refresh raced a concurrent commit on main — e.g. a PR merge or watcher)."""
    from scripts import refresh_leaderboard

    calls = []
    monkeypatch.setattr(
        refresh_leaderboard.subprocess,
        "run",
        _make_fake_run(calls, push_returncodes=[1, 0]),
    )

    refresh_leaderboard._push_with_rebase_retry(max_attempts=3)

    pushes = [c for c in calls if c[:2] == ["git", "push"]]
    rebases = [c for c in calls if c[:3] == ["git", "pull", "--rebase"]]
    assert len(pushes) == 2, "should retry the push exactly once after rejection"
    assert len(rebases) == 1, "should rebase on origin/main between attempts"


def test_push_raises_after_max_attempts(monkeypatch):
    """If every push is rejected, give up loudly after max_attempts (no rebase
    after the final attempt)."""
    from scripts import refresh_leaderboard

    calls = []
    monkeypatch.setattr(
        refresh_leaderboard.subprocess,
        "run",
        _make_fake_run(calls, push_returncodes=[1, 1, 1]),
    )

    with pytest.raises(RuntimeError):
        refresh_leaderboard._push_with_rebase_retry(max_attempts=3)

    pushes = [c for c in calls if c[:2] == ["git", "push"]]
    rebases = [c for c in calls if c[:3] == ["git", "pull", "--rebase"]]
    assert len(pushes) == 3
    assert len(rebases) == 2


def test_refresh_leaderboard_writes_current_json(midas_data_root, monkeypatch):
    """End-to-end: snapshots → baselines → current.json, all in the isolated tmp root."""
    from engine.config import get_config
    from scripts import refresh_leaderboard

    get_config().leaderboard_dir.mkdir(parents=True, exist_ok=True)

    calls = []
    monkeypatch.setattr(
        refresh_leaderboard,
        "_step_fetch_market_data",
        lambda: {"date": "2026-05-23", "benchmarks": {}},
    )
    monkeypatch.setattr(
        refresh_leaderboard,
        "_step_update_snapshots",
        lambda payload: calls.append(("snapshots", payload["date"])) or ["satoshi"],
    )
    monkeypatch.setattr(
        refresh_leaderboard,
        "_step_build_baselines",
        lambda: calls.append(("baselines",)),
    )
    monkeypatch.setattr(
        refresh_leaderboard,
        "_build_portfolio_summaries",
        lambda: {"satoshi": {"agent_id": "satoshi"}},
    )
    monkeypatch.setattr(
        refresh_leaderboard,
        "_build_leaderboard_rows",
        lambda summaries, on: [{"rank": 1, "agent": "satoshi", "return_pct": 3.0}],
    )

    # Capture mtimes of committed tax_shadow files before the run to prove
    # isolation: _step_build_tax_shadow must NOT touch the real data directory.
    real_tax_shadow_dir = _REAL_PROJECT_ROOT / "data" / "tax_shadow"
    real_mtimes_before = (
        {
            p.name: p.stat().st_mtime
            for p in real_tax_shadow_dir.iterdir()
            if p.is_file()
        }
        if real_tax_shadow_dir.exists()
        else {}
    )

    refresh_leaderboard.run(
        trigger="scheduled-weekend-refresh", today=date(2026, 5, 23)
    )

    assert ("snapshots", "2026-05-23") in calls
    assert ("baselines",) in calls
    leaderboard_path = get_config().leaderboard_dir / "current.json"
    payload = json.loads(leaderboard_path.read_text())
    assert payload["trigger"] == "scheduled-weekend-refresh"
    assert payload["rows"][0]["agent"] == "satoshi"
    assert payload["updated_at"].endswith("Z")

    # Isolation check: committed tax_shadow files must be untouched.
    real_mtimes_after = (
        {
            p.name: p.stat().st_mtime
            for p in real_tax_shadow_dir.iterdir()
            if p.is_file()
        }
        if real_tax_shadow_dir.exists()
        else {}
    )
    assert real_mtimes_before == real_mtimes_after, (
        "_step_build_tax_shadow wrote to the real data/tax_shadow/ directory "
        "instead of the tmp_path sandbox — path injection is broken."
    )
