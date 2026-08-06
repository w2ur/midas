"""Append-or-refuse contract for baseline files.

Background: `build_all_baselines` used to full-rewrite every baseline file
from `cfg.day_one` on every session, while `PortfolioManager.add_snapshot`
refuses to let a later session replace an already-published row. A revised
OHLCV price therefore silently moved the benchmark curve while the agent
curve stayed frozen — both plotted on the same dossier chart.

`merge_baseline_series` closes that gap: a published date is kept unless
`restate=True` is passed explicitly (the one-time, owner-approved
restatement escape hatch). New dates are always appended.
"""

from __future__ import annotations

import json
from pathlib import Path

from engine.baselines import merge_baseline_series


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n")


def test_merge_baseline_series_appends_new_dates(tmp_path):
    path = tmp_path / "benchmark.json"
    _write(
        path,
        [
            {
                "date": "2026-08-04",
                "portfolio_value": 100.0,
                "cash": 0.0,
                "positions_value": 100.0,
                "currency": "EUR",
            }
        ],
    )
    computed = [
        {
            "date": "2026-08-04",
            "portfolio_value": 100.0,
            "cash": 0.0,
            "positions_value": 100.0,
            "currency": "EUR",
        },
        {
            "date": "2026-08-05",
            "portfolio_value": 101.0,
            "cash": 0.0,
            "positions_value": 101.0,
            "currency": "EUR",
        },
    ]

    assert merge_baseline_series(path, computed) == (1, 0)
    on_disk = json.loads(path.read_text())
    assert [row["date"] for row in on_disk] == ["2026-08-04", "2026-08-05"]


def test_merge_baseline_series_refuses_to_move_a_published_point(tmp_path):
    """Regression: pre-fix, build_all_baselines full-rewrote from day one every
    session while snapshots were append-or-refuse, so a revised price silently
    moved the benchmark curve under a frozen agent curve."""
    path = tmp_path / "benchmark.json"
    _write(
        path,
        [
            {
                "date": "2026-08-04",
                "portfolio_value": 8695.39,
                "cash": 0.0,
                "positions_value": 8695.39,
                "currency": "EUR",
            }
        ],
    )
    computed = [
        {
            "date": "2026-08-04",
            "portfolio_value": 8679.04,
            "cash": 0.0,
            "positions_value": 8679.04,
            "currency": "EUR",
        }
    ]

    appended, refused = merge_baseline_series(path, computed)

    assert (appended, refused) == (0, 1)
    assert json.loads(path.read_text())[0]["portfolio_value"] == 8695.39


def test_merge_baseline_series_restate_flag_overwrites(tmp_path):
    """The one-time restatement path — used deliberately, logged publicly."""
    path = tmp_path / "benchmark.json"
    _write(
        path,
        [
            {
                "date": "2026-08-04",
                "portfolio_value": 8695.39,
                "cash": 0.0,
                "positions_value": 8695.39,
                "currency": "EUR",
            }
        ],
    )
    computed = [
        {
            "date": "2026-08-04",
            "portfolio_value": 8679.04,
            "cash": 0.0,
            "positions_value": 8679.04,
            "currency": "EUR",
        }
    ]

    assert merge_baseline_series(path, computed, restate=True) == (0, 0)
    assert json.loads(path.read_text())[0]["portfolio_value"] == 8679.04


def test_merge_baseline_series_creates_file_when_none_exists(tmp_path):
    """First-ever build for a fresh agent dir: no prior file to refuse against."""
    path = tmp_path / "benchmark.json"
    computed = [
        {
            "date": "2026-08-04",
            "portfolio_value": 100.0,
            "cash": 0.0,
            "positions_value": 100.0,
            "currency": "EUR",
        }
    ]

    assert merge_baseline_series(path, computed) == (1, 0)
    assert json.loads(path.read_text()) == computed


def test_merge_baseline_series_identical_replay_is_not_a_refusal(tmp_path):
    """Re-running the same session with unchanged prices must not warn."""
    path = tmp_path / "benchmark.json"
    rows = [
        {
            "date": "2026-08-04",
            "portfolio_value": 100.0,
            "cash": 0.0,
            "positions_value": 100.0,
            "currency": "EUR",
        }
    ]
    _write(path, rows)

    assert merge_baseline_series(path, rows) == (0, 0)
