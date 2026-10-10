"""The checkout fence around a persona dispatch round."""

from __future__ import annotations

import json
import os
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
    (tmp_path / "engine").mkdir()
    (tmp_path / "engine" / "x.py").write_text("X = 1\n")
    (tmp_path / ".gitignore").write_text(
        "data/cache/\ndata/session_state/\ndata/market/today.json\n"
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


@pytest.fixture(autouse=True)
def site_packages(tmp_path_factory, monkeypatch) -> Path:
    """A private site-packages, so the real venv never enters a test's snapshot."""
    site = tmp_path_factory.mktemp("site-packages")
    (site / "existing.pth").write_text("/some/path\n")
    monkeypatch.setattr("engine.dispatch_guard._site_packages_dirs", lambda: [site])
    return site


def test_a_round_that_writes_nothing_passes(repo, capsys) -> None:
    token = snapshot_data_tree("r", repo)
    assert_data_tree_unchanged("r", token, repo)
    assert "unchanged" in capsys.readouterr().out


def test_a_new_file_under_data_is_named(repo) -> None:
    token = snapshot_data_tree("r", repo)
    (repo / "data" / "planted.json").write_text("evil")
    with pytest.raises(DispatchWroteDataError, match=r"data/planted\.json"):
        assert_data_tree_unchanged("r", token, repo)


def test_a_modified_tracked_file_is_named(repo) -> None:
    token = snapshot_data_tree("r", repo)
    (repo / "data" / "tracked.json").write_text('{"a": 1}\n')
    with pytest.raises(DispatchWroteDataError, match=r"data/tracked\.json"):
        assert_data_tree_unchanged("r", token, repo)


def test_a_second_edit_of_an_already_dirty_tracked_file_is_named(repo) -> None:
    (repo / "data" / "tracked.json").write_text('{"a": 1}\n')
    token = snapshot_data_tree("r", repo)
    (repo / "data" / "tracked.json").write_text('{"a": 2}\n')
    with pytest.raises(DispatchWroteDataError, match=r"data/tracked\.json"):
        assert_data_tree_unchanged("r", token, repo)


def test_an_edited_untracked_file_and_a_deleted_one_are_named(repo) -> None:
    (repo / "data" / "pre.json").write_text("a")
    token = snapshot_data_tree("r", repo)
    (repo / "data" / "pre.json").write_text("b")
    (repo / "data" / "tracked.json").unlink()
    with pytest.raises(DispatchWroteDataError) as err:
        assert_data_tree_unchanged("r", token, repo)
    assert "data/pre.json" in str(err.value)
    assert "data/tracked.json" in str(err.value)


def test_gitignored_paths_are_outside_the_fence(repo) -> None:
    token = snapshot_data_tree("r", repo)
    (repo / "data" / "cache").mkdir()
    (repo / "data" / "cache" / "x").write_text("ignored")
    assert_data_tree_unchanged("r", token, repo)


def test_a_write_to_ignored_session_state_is_named(repo) -> None:
    state = repo / "data" / "session_state"
    (state / "prompts").mkdir(parents=True)
    (state / "prompts" / "sibling.txt").write_text("honest prompt")
    token = snapshot_data_tree("r", repo)
    (state / "prompts" / "sibling.txt").write_text("injected prompt")
    (state / "step2.done").write_text("marker")
    with pytest.raises(DispatchWroteDataError) as err:
        assert_data_tree_unchanged("r", token, repo)
    assert "data/session_state/prompts/sibling.txt" in str(err.value)
    assert "data/session_state/step2.done" in str(err.value)


def test_an_edit_of_a_tracked_file_outside_data_is_named(repo) -> None:
    token = snapshot_data_tree("r", repo)
    (repo / "engine" / "x.py").write_text("X = 2\n")
    with pytest.raises(DispatchWroteDataError, match=r"engine/x\.py"):
        assert_data_tree_unchanged("r", token, repo)


def test_a_new_untracked_file_outside_data_is_named(repo) -> None:
    token = snapshot_data_tree("r", repo)
    (repo / "roster.yaml").write_text("agents: []\n")
    with pytest.raises(DispatchWroteDataError, match=r"roster\.yaml"):
        assert_data_tree_unchanged("r", token, repo)


def test_a_success_deletes_the_snapshot_so_a_second_end_raises(repo) -> None:
    token = snapshot_data_tree("r", repo)
    assert_data_tree_unchanged("r", token, repo)
    with pytest.raises(DispatchWroteDataError, match="did not run"):
        assert_data_tree_unchanged("r", token, repo)


def test_a_missing_snapshot_raises(repo) -> None:
    with pytest.raises(DispatchWroteDataError, match="did not run"):
        assert_data_tree_unchanged("never-taken", "0" * 64, repo)


def test_the_snapshot_lives_outside_data(repo) -> None:
    token = snapshot_data_tree("r", repo)
    assert not any(p.name == "r.json" for p in (repo / "data").rglob("*"))
    assert (repo / ".git" / "midas-dispatch-guard" / "r.json").exists()


def test_session_step_wrappers_delegate(repo, monkeypatch) -> None:
    monkeypatch.setattr("engine.dispatch_guard._REPO_ROOT", repo)
    token = ds.step_guard_dispatch_begin("step2-trading")
    (repo / "data" / "planted.json").write_text("evil")
    with pytest.raises(DispatchWroteDataError, match=r"data/planted\.json"):
        ds.step_guard_dispatch_end("step2-trading", token)


def test_a_clean_round_passes_and_deletes_its_snapshot(repo) -> None:
    token = snapshot_data_tree("r", repo)
    snap = repo / ".git" / "midas-dispatch-guard" / "r.json"
    assert snap.is_file()
    assert_data_tree_unchanged("r", token, repo)
    assert not snap.exists()


def test_a_commit_made_during_the_round_is_named(repo) -> None:
    """A subagent commit leaves status clean; step_git_commit_push would push it."""
    token = snapshot_data_tree("r", repo)
    (repo / "engine" / "x.py").write_text("X = 2\n")
    _git(repo, "commit", "-q", "-am", "planted")
    assert subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True, capture_output=True,
    ).stdout == b""
    with pytest.raises(DispatchWroteDataError, match=r"HEAD \(changed\)"):
        assert_data_tree_unchanged("r", token, repo)


def test_a_written_file_hidden_by_skip_worktree_is_named(repo) -> None:
    token = snapshot_data_tree("r", repo)
    (repo / "data" / "tracked.json").write_text('{"evil": 1}\n')
    _git(repo, "update-index", "--skip-worktree", "data/tracked.json")
    with pytest.raises(
        DispatchWroteDataError, match=r"index-flag:data/tracked\.json \(appeared\)"
    ):
        assert_data_tree_unchanged("r", token, repo)


def test_a_new_file_hidden_by_info_exclude_is_named(repo) -> None:
    token = snapshot_data_tree("r", repo)
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(exist_ok=True)
    with exclude.open("a") as fh:
        fh.write("data/planted.json\n")
    (repo / "data" / "planted.json").write_text("evil")
    with pytest.raises(DispatchWroteDataError, match=r"git:info/exclude"):
        assert_data_tree_unchanged("r", token, repo)


def test_a_new_git_hook_is_named(repo) -> None:
    token = snapshot_data_tree("r", repo)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\ncurl evil\n")
    with pytest.raises(
        DispatchWroteDataError, match=r"git:hooks/pre-commit \(appeared\)"
    ):
        assert_data_tree_unchanged("r", token, repo)


def test_a_git_config_change_is_named(repo) -> None:
    token = snapshot_data_tree("r", repo)
    _git(repo, "config", "core.hooksPath", "/tmp/elsewhere")
    with pytest.raises(DispatchWroteDataError, match=r"git:config \(changed\)"):
        assert_data_tree_unchanged("r", token, repo)


def test_a_rewritten_snapshot_is_refused_with_the_original_token(repo) -> None:
    token = snapshot_data_tree("r", repo)
    (repo / "data" / "planted.json").write_text("evil")
    snap = repo / ".git" / "midas-dispatch-guard" / "r.json"
    doc = json.loads(snap.read_text())
    doc["paths"]["data/planted.json"] = "anything"
    snap.write_text(json.dumps(doc))
    with pytest.raises(DispatchWroteDataError, match="rewritten"):
        assert_data_tree_unchanged("r", token, repo)


def test_an_edit_of_ignored_today_json_is_named(repo) -> None:
    today = repo / "data" / "market" / "today.json"
    today.parent.mkdir()
    today.write_text('{"SPY": 500}\n')
    token = snapshot_data_tree("r", repo)
    today.write_text('{"SPY": 5}\n')
    with pytest.raises(DispatchWroteDataError, match=r"data/market/today\.json"):
        assert_data_tree_unchanged("r", token, repo)


def test_a_new_pth_file_in_site_packages_is_named(repo, site_packages) -> None:
    token = snapshot_data_tree("r", repo)
    (site_packages / "zz-evil.pth").write_text("import os; os.system('x')\n")
    with pytest.raises(
        DispatchWroteDataError, match=r"site-packages:zz-evil\.pth \(appeared\)"
    ):
        assert_data_tree_unchanged("r", token, repo)


def test_a_staged_rename_names_both_paths(repo) -> None:
    token = snapshot_data_tree("r", repo)
    _git(repo, "mv", "data/tracked.json", "data/renamed.json")
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert status.startswith("R ")  # the R-entry branch is what this exercises
    with pytest.raises(DispatchWroteDataError) as err:
        assert_data_tree_unchanged("r", token, repo)
    assert "data/renamed.json (appeared)" in str(err.value)
    assert "data/tracked.json (appeared)" in str(err.value)


def test_a_staged_change_to_an_already_dirty_file_is_named(repo) -> None:
    (repo / "engine" / "x.py").write_text("X = 2\n")
    token = snapshot_data_tree("r", repo)
    _git(repo, "add", "engine/x.py")
    with pytest.raises(
        DispatchWroteDataError, match=r"index-status:engine/x\.py \(changed\)"
    ):
        assert_data_tree_unchanged("r", token, repo)


def test_end_without_a_token_raises(repo) -> None:
    snapshot_data_tree("r", repo)
    with pytest.raises(DispatchWroteDataError, match="no dispatch-guard token"):
        assert_data_tree_unchanged("r", None, repo)


def test_session_end_wrapper_without_a_token_raises(repo, monkeypatch) -> None:
    monkeypatch.setattr("engine.dispatch_guard._REPO_ROOT", repo)
    ds.step_guard_dispatch_begin("step2-trading")
    with pytest.raises(DispatchWroteDataError, match="no dispatch-guard token"):
        ds.step_guard_dispatch_end("step2-trading")


def test_end_with_a_wrong_token_raises_and_keeps_the_snapshot(repo) -> None:
    snapshot_data_tree("r", repo)
    with pytest.raises(DispatchWroteDataError, match="rewritten"):
        assert_data_tree_unchanged("r", "0" * 64, repo)
    assert (repo / ".git" / "midas-dispatch-guard" / "r.json").is_file()


def test_an_error_while_checking_is_a_dispatch_error_not_a_pass(
    repo, monkeypatch
) -> None:
    token = snapshot_data_tree("r", repo)

    def broken(*args, **kwargs):
        raise subprocess.CalledProcessError(128, ["git", "status"])

    monkeypatch.setattr("engine.dispatch_guard._git", broken)
    with pytest.raises(DispatchWroteDataError, match="could not be evaluated"):
        assert_data_tree_unchanged("r", token, repo)


_NON_UTF8 = b"data/caf\xe9.json"


def _make_non_utf8_file(repo: Path) -> None:
    try:
        with open(os.fsencode(repo) + b"/" + _NON_UTF8, "wb") as fh:
            fh.write(b"x")
    except OSError as exc:
        pytest.skip(f"this filesystem refuses non-UTF-8 file names ({exc})")


def test_a_non_utf8_name_before_begin_does_not_crash_begin(repo) -> None:
    _make_non_utf8_file(repo)
    token = snapshot_data_tree("r", repo)
    assert_data_tree_unchanged("r", token, repo)


def test_a_non_utf8_name_created_during_the_round_is_a_dispatch_error(repo) -> None:
    token = snapshot_data_tree("r", repo)
    _make_non_utf8_file(repo)
    with pytest.raises(DispatchWroteDataError, match=r"caf\\xe9\.json"):
        assert_data_tree_unchanged("r", token, repo)


def _status_also_lists(monkeypatch, entry: bytes) -> None:
    """Make ``git status`` report a non-UTF-8 path, on any filesystem."""
    import engine.dispatch_guard as guard

    real = guard._git

    def fake(root, *args):
        out = real(root, *args)
        return out + entry if args[:1] == ("status",) else out

    monkeypatch.setattr(guard, "_git", fake)


def test_a_reported_non_utf8_path_before_begin_does_not_crash_begin(
    repo, monkeypatch
) -> None:
    _status_also_lists(monkeypatch, b"?? " + _NON_UTF8 + b"\0")
    token = snapshot_data_tree("r", repo)
    assert_data_tree_unchanged("r", token, repo)


def test_a_reported_non_utf8_path_during_the_round_is_a_dispatch_error(
    repo, monkeypatch
) -> None:
    token = snapshot_data_tree("r", repo)
    _status_also_lists(monkeypatch, b"?? " + _NON_UTF8 + b"\0")
    with pytest.raises(DispatchWroteDataError, match=r"caf\\xe9\.json \(appeared\)"):
        assert_data_tree_unchanged("r", token, repo)


def test_session_begin_prints_its_token_for_a_later_process(
    repo, monkeypatch, capsys
) -> None:
    """The orchestrator may call end from a new Python process."""
    monkeypatch.setattr("engine.dispatch_guard._REPO_ROOT", repo)
    token = ds.step_guard_dispatch_begin("step6-posts")
    assert f"dispatch guard token: {token}" in capsys.readouterr().out
    ds.step_guard_dispatch_end("step6-posts", token)
