"""Step 9 ran ⇔ the baseline series reaches the snapshot date.

The `session-integrity` `check` job asserted Step 9 by grepping the commit's
changed-file list for `^data/baselines/`. That is a proxy for the thing it
cares about, and on 2026-08-07 the proxy and the thing came apart: commit
`a4dc9dce2 [restate]` rebuilt every baseline series earlier the same day, so
when the evening session ran Step 9 against an unchanged store,
`merge_baseline_series` (append-or-refuse) correctly kept the existing rows and
the commit touched `data/baselines/` not at all. Both `session-integrity` and
the inline copy in `auto-merge-session` failed a session whose Step 9 had run
exactly as designed — and the auto-merge one would have blocked the session
from reaching main had the direct push not already succeeded.

A diff cannot answer "did Step 9 run"; the published state can. If Step 9 is
genuinely skipped, snapshots gain a point that the baselines do not, and the
two series come apart — which is the Apr 25 shape the guard was built for.
If Step 9 runs and correctly writes nothing, they stay level.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_session_freshness import stale_baselines

AGENT = "goldfinger"


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows), encoding="utf-8")


def _desk(root: Path, *, snapshot_dates: list[str], baseline_dates: list[str]) -> Path:
    """A one-agent desk plus the global series, at the given dates."""
    _write(
        root / "data" / "portfolios" / AGENT / "snapshots.json",
        [
            {"date": d, "session_date": d, "portfolio_value": 1.0}
            for d in snapshot_dates
        ],
    )
    for kind in ("benchmark", "coinflip"):
        _write(
            root / "data" / "baselines" / AGENT / f"{kind}.json",
            [{"date": d, "portfolio_value": 1.0} for d in baseline_dates],
        )
    _write(
        root / "data" / "baselines" / "global" / "msci_world.json",
        [{"date": d, "portfolio_value": 1.0} for d in baseline_dates],
    )
    return root


def test_baselines_level_with_snapshots_are_current(tmp_path: Path) -> None:
    root = _desk(
        tmp_path,
        snapshot_dates=["2026-08-06", "2026-08-07"],
        baseline_dates=["2026-08-06", "2026-08-07"],
    )
    assert stale_baselines(root) == []


def test_baselines_lagging_snapshots_are_stale(tmp_path: Path) -> None:
    """Step 9 genuinely skipped: the snapshot advanced, the baseline did not."""
    root = _desk(
        tmp_path,
        snapshot_dates=["2026-08-06", "2026-08-07"],
        baseline_dates=["2026-08-06"],
    )
    assert stale_baselines(root) == [AGENT, "global"]


def test_a_series_rebuilt_earlier_the_same_day_is_current(tmp_path: Path) -> None:
    """Regression: the 2026-08-07 false positive.

    The baseline already carries the session's date because an earlier
    `[restate]` commit wrote it. Step 9 ran, `merge_baseline_series` refused to
    move a published row, and the commit's diff is empty — which is precisely
    what the path-diff proxy read as "Step 9 skipped".
    """
    root = _desk(
        tmp_path,
        snapshot_dates=["2026-08-06", "2026-08-07"],
        baseline_dates=["2026-08-06", "2026-08-07"],
    )
    # No file is touched between the restatement and the check — the freshness
    # question is answered from state, so an empty diff is not evidence.
    assert stale_baselines(root) == []


def test_a_store_that_did_not_advance_is_current(tmp_path: Path) -> None:
    """Neither series moves when the store is unchanged. That is not a skip."""
    root = _desk(
        tmp_path,
        snapshot_dates=["2026-08-06"],
        baseline_dates=["2026-08-06"],
    )
    assert stale_baselines(root) == []


def test_a_baseline_ahead_of_its_snapshots_is_current(tmp_path: Path) -> None:
    """A restatement may publish a baseline point the book has not reached."""
    root = _desk(
        tmp_path,
        snapshot_dates=["2026-08-06"],
        baseline_dates=["2026-08-06", "2026-08-07"],
    )
    assert stale_baselines(root) == []


def test_a_missing_baseline_series_is_stale(tmp_path: Path) -> None:
    root = _desk(
        tmp_path,
        snapshot_dates=["2026-08-07"],
        baseline_dates=["2026-08-07"],
    )
    (root / "data" / "baselines" / AGENT / "benchmark.json").unlink()
    assert AGENT in stale_baselines(root)


def test_an_agent_with_no_snapshots_is_not_reported(tmp_path: Path) -> None:
    """A book that has never been valued cannot have a lagging baseline."""
    root = _desk(
        tmp_path,
        snapshot_dates=["2026-08-07"],
        baseline_dates=["2026-08-07"],
    )
    _write(root / "data" / "portfolios" / "newcomer" / "snapshots.json", [])
    assert "newcomer" not in stale_baselines(root)


@pytest.mark.parametrize("kind", ["benchmark", "coinflip"])
def test_either_series_lagging_marks_the_agent_stale(tmp_path: Path, kind: str) -> None:
    """Both series are Step 9's output; one lagging is still a skip."""
    root = _desk(
        tmp_path,
        snapshot_dates=["2026-08-07"],
        baseline_dates=["2026-08-07"],
    )
    _write(
        root / "data" / "baselines" / AGENT / f"{kind}.json",
        [{"date": "2026-08-06", "portfolio_value": 1.0}],
    )
    assert AGENT in stale_baselines(root)
