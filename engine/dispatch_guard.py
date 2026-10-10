"""Fence ``data/`` across a persona dispatch round.

Dispatched agents now hold live web tools, and what a page returns is untrusted
text that may try to make them write a file. The prompt says not to; this is the
part that does not rely on the agent obeying. ``snapshot_data_tree`` records
the state of everything under ``data/`` that git does not ignore, and
``assert_data_tree_unchanged`` refuses if a dispatch round changed any of it.

The snapshot lives under the git dir, outside ``data/``, so the guard can never
observe itself. A missing snapshot raises: an unevaluated guard is not a passed
guard.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GUARD_DIRNAME = "midas-dispatch-guard"


class DispatchWroteDataError(RuntimeError):
    """A dispatch round changed files under ``data/``."""


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True
    ).stdout


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot_path(round_name: str, root: Path) -> Path:
    git_dir = Path(_git(root, "rev-parse", "--absolute-git-dir").decode().strip())
    return git_dir / _GUARD_DIRNAME / f"{round_name}.json"


def _capture(root: Path) -> dict:
    status = _git(
        root, "status", "--porcelain", "--untracked-files=all", "--", "data/"
    ).decode()
    tracked_paths = [
        p
        for p in _git(root, "diff", "HEAD", "--name-only", "-z", "--", "data/")
        .decode()
        .split("\0")
        if p
    ]
    tracked = {
        p: _sha(_git(root, "diff", "HEAD", "--binary", "--", p)) for p in tracked_paths
    }
    untracked_paths = [
        p
        for p in _git(
            root, "ls-files", "--others", "--exclude-standard", "-z", "--", "data/"
        )
        .decode()
        .split("\0")
        if p
    ]
    untracked = {p: _sha((root / p).read_bytes()) for p in untracked_paths}
    return {
        "status": status.splitlines(),
        "diff_sha256": _sha(_git(root, "diff", "HEAD", "--binary", "--", "data/")),
        "tracked": tracked,
        "untracked": untracked,
    }


def snapshot_data_tree(round_name: str, repo_root: Path | None = None) -> None:
    """Record the state of ``data/`` before a dispatch round."""
    root = repo_root or _REPO_ROOT
    snap = _capture(root)
    path = _snapshot_path(round_name, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"  dispatch guard [{round_name}]: snapshot taken "
        f"({len(snap['tracked'])} modified, {len(snap['untracked'])} untracked)"
    )


def _diff(before: dict[str, str], after: dict[str, str], kind: str) -> list[str]:
    out = [f"{p} ({kind} appeared)" for p in sorted(after.keys() - before.keys())]
    out += [f"{p} ({kind} disappeared)" for p in sorted(before.keys() - after.keys())]
    out += [
        f"{p} ({kind} changed)"
        for p in sorted(before.keys() & after.keys())
        if before[p] != after[p]
    ]
    return out


def assert_data_tree_unchanged(round_name: str, repo_root: Path | None = None) -> None:
    """Raise ``DispatchWroteDataError`` naming every path the round changed."""
    root = repo_root or _REPO_ROOT
    path = _snapshot_path(round_name, root)
    if not path.is_file():
        raise DispatchWroteDataError(
            f"no dispatch-guard snapshot for round {round_name!r} at {path}: "
            "the guard did not run, which is not the same as the round being clean"
        )
    before = json.loads(path.read_text(encoding="utf-8"))
    after = _capture(root)
    problems = _diff(before["tracked"], after["tracked"], "tracked file")
    problems += _diff(before["untracked"], after["untracked"], "untracked file")
    if not problems and (
        before["diff_sha256"] != after["diff_sha256"]
        or before["status"] != after["status"]
    ):
        problems.append("data/ changed in a way no single path accounts for")
    if problems:
        raise DispatchWroteDataError(
            f"dispatch round {round_name!r} wrote under data/: " + "; ".join(problems)
        )
    print(f"  dispatch guard [{round_name}]: data/ unchanged")
