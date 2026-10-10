"""The data/ fence around a persona dispatch round."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import scripts.daily_session as ds
from engine.dispatch_guard import (
    DispatchWroteDataError,
    assert_data_tree_unchanged,
    snapshot_data_tree,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "tracked.json").write_text("{}\n")
    (tmp_path / ".gitignore").write_text("data/cache/\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_a_round_that_writes_nothing_passes(repo, capsys) -> None:
    snapshot_data_tree("r", repo)
    assert_data_tree_unchanged("r", repo)
    assert "unchanged" in capsys.readouterr().out


def test_a_new_file_under_data_is_named(repo) -> None:
    snapshot_data_tree("r", repo)
    (repo / "data" / "planted.json").write_text("evil")
    with pytest.raises(DispatchWroteDataError, match=r"data/planted\.json"):
        assert_data_tree_unchanged("r", repo)


def test_a_modified_tracked_file_is_named(repo) -> None:
    snapshot_data_tree("r", repo)
    (repo / "data" / "tracked.json").write_text('{"a": 1}\n')
    with pytest.raises(DispatchWroteDataError, match=r"data/tracked\.json"):
        assert_data_tree_unchanged("r", repo)


def test_a_second_edit_of_an_already_dirty_tracked_file_is_named(repo) -> None:
    (repo / "data" / "tracked.json").write_text('{"a": 1}\n')
    snapshot_data_tree("r", repo)
    (repo / "data" / "tracked.json").write_text('{"a": 2}\n')
    with pytest.raises(DispatchWroteDataError, match=r"data/tracked\.json"):
        assert_data_tree_unchanged("r", repo)


def test_an_edited_untracked_file_and_a_deleted_one_are_named(repo) -> None:
    (repo / "data" / "pre.json").write_text("a")
    snapshot_data_tree("r", repo)
    (repo / "data" / "pre.json").write_text("b")
    (repo / "data" / "tracked.json").unlink()
    with pytest.raises(DispatchWroteDataError) as err:
        assert_data_tree_unchanged("r", repo)
    assert "data/pre.json" in str(err.value)
    assert "data/tracked.json" in str(err.value)


def test_gitignored_paths_are_outside_the_fence(repo) -> None:
    snapshot_data_tree("r", repo)
    (repo / "data" / "cache").mkdir()
    (repo / "data" / "cache" / "x").write_text("ignored")
    assert_data_tree_unchanged("r", repo)


def test_a_missing_snapshot_raises(repo) -> None:
    with pytest.raises(DispatchWroteDataError, match="did not run"):
        assert_data_tree_unchanged("never-taken", repo)


def test_the_snapshot_lives_outside_data(repo) -> None:
    snapshot_data_tree("r", repo)
    assert not any(p.name == "r.json" for p in (repo / "data").rglob("*"))
    assert (repo / ".git" / "midas-dispatch-guard" / "r.json").exists()


def test_session_step_wrappers_delegate(repo, monkeypatch) -> None:
    monkeypatch.setattr("engine.dispatch_guard._REPO_ROOT", repo)
    ds.step_guard_dispatch_begin("step2-trading")
    (repo / "data" / "planted.json").write_text("evil")
    with pytest.raises(DispatchWroteDataError, match=r"data/planted\.json"):
        ds.step_guard_dispatch_end("step2-trading")
