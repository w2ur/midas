import json
from datetime import date
from pathlib import Path

# Resolve project root so we can probe the committed tax_shadow dir.
_REAL_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_refresh_leaderboard_writes_current_json(tmp_path, monkeypatch):
    """End-to-end: snapshots → baselines → current.json, all in tmp_path."""
    from scripts import refresh_leaderboard

    (tmp_path / "data" / "leaderboard").mkdir(parents=True)
    monkeypatch.setattr(refresh_leaderboard, "_PROJECT_ROOT", tmp_path)

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
    payload = json.loads(
        (tmp_path / "data" / "leaderboard" / "current.json").read_text()
    )
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
