"""The bundle's leaderboard and current.json are one leaderboard.

Regression: #116 (2026-10-13). The session built the rows at Step 5, before the
baselines the board ranks on were refreshed, then rebuilt them for current.json
after Step 9. The archive bundle ranked steady-eddie-usd first and the live
board ranked satoshi first, and nothing compared the two.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_leaderboard_consistency import (
    STAMP_KEY,
    first_difference,
    main,
    newest_bundle,
)

ROWS = [
    {"rank": 1, "agent": "steady-eddie-usd", "return_pct": 19.38, "vs_benchmark_pp": 6.1},
    {"rank": 2, "agent": "satoshi", "return_pct": 16.29, "vs_benchmark_pp": 4.2},
]


def _desk(
    root: Path,
    bundle_rows: list[dict],
    current_rows: list[dict],
    *,
    stamped: bool = True,
) -> Path:
    (root / "data" / "output").mkdir(parents=True)
    (root / "data" / "leaderboard").mkdir(parents=True)
    bundle: dict = {"date": "2026-10-13", "leaderboard": bundle_rows}
    if stamped:
        bundle[STAMP_KEY] = True
    (root / "data" / "output" / "2026-10-13.json").write_text(json.dumps(bundle))
    (root / "data" / "leaderboard" / "current.json").write_text(
        json.dumps({"trigger": "session-2026-10-13", "rows": current_rows})
    )
    return root


def _run(root: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr("sys.argv", ["check", "--root", str(root)])
    return main()


def test_identical_rows_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _desk(tmp_path, ROWS, [dict(r) for r in ROWS])
    assert _run(tmp_path, monkeypatch) == 0


def test_a_different_rank_order_fails_and_names_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The 2026-10-13 shape: same agents, a different one ranked first.
    _desk(tmp_path, ROWS, [ROWS[1], ROWS[0]])
    assert _run(tmp_path, monkeypatch) == 1
    err = capsys.readouterr().err
    assert "row 1 differs" in err
    assert "steady-eddie-usd" in err and "satoshi" in err


def test_a_changed_number_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    drifted = [dict(ROWS[0], vs_benchmark_pp=6.2), ROWS[1]]
    _desk(tmp_path, ROWS, drifted)
    assert _run(tmp_path, monkeypatch) == 1


def test_a_missing_row_fails() -> None:
    assert "2 rows" in (first_difference(ROWS, ROWS[:1]) or "")


def test_a_missing_current_json_is_unknown_not_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _desk(tmp_path, ROWS, ROWS)
    (tmp_path / "data" / "leaderboard" / "current.json").unlink()
    assert _run(tmp_path, monkeypatch) == 2


def test_no_bundle_is_unknown_not_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "data" / "output").mkdir(parents=True)
    assert _run(tmp_path, monkeypatch) == 2


def test_a_bundle_without_a_leaderboard_list_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _desk(tmp_path, ROWS, ROWS)
    (tmp_path / "data" / "output" / "2026-10-13.json").write_text(
        json.dumps({STAMP_KEY: True})
    )
    assert _run(tmp_path, monkeypatch) == 2


def test_an_old_prompt_bundle_is_skipped_with_a_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Sessions on the pre-4c prompt order routinely differ; merging the check
    # before the owner re-pastes the prompt must not turn CI red.
    _desk(tmp_path, ROWS, [ROWS[1], ROWS[0]], stamped=False)
    assert _run(tmp_path, monkeypatch) == 0
    assert "skipped" in capsys.readouterr().err


def test_a_new_prompt_bundle_with_equal_rows_passes_when_stamped_by_the_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _desk(tmp_path, ROWS, [dict(r) for r in ROWS], stamped=False)
    monkeypatch.setattr("sys.argv", ["check", "--root", str(tmp_path), "--stamp"])
    assert main() == 0
    bundle = json.loads((tmp_path / "data" / "output" / "2026-10-13.json").read_text())
    assert bundle[STAMP_KEY] is True
    assert _run(tmp_path, monkeypatch) == 0


def test_a_new_prompt_bundle_with_differing_rows_fails_once_stamped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _desk(tmp_path, ROWS, [ROWS[1], ROWS[0]], stamped=False)
    monkeypatch.setattr("sys.argv", ["check", "--root", str(tmp_path), "--stamp"])
    assert main() == 1
    assert _run(tmp_path, monkeypatch) == 1


def test_the_newest_dated_bundle_is_the_one_compared(tmp_path: Path) -> None:
    out = tmp_path / "output"
    out.mkdir()
    for name in ("2026-10-12.json", "2026-10-13.json", "notes.json"):
        (out / name).write_text("{}")
    assert newest_bundle(out) == out / "2026-10-13.json"


def test_session_integrity_runs_the_check_on_the_session_commit() -> None:
    """A guard nobody runs cannot go red: the workflow must call the script."""
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "session-integrity.yml"
    ).read_text(encoding="utf-8")
    assert "scripts/check_leaderboard_consistency.py" in workflow
