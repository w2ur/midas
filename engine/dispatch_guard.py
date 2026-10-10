"""Fence the repository checkout across a persona dispatch round.

Dispatched agents now hold live web tools, and what a page returns is untrusted
text that may try to make them write a file. The prompt says not to; this is the
part that does not rely on the agent obeying. ``snapshot_data_tree`` fingerprints
every path git lists as modified, added or untracked (so ``engine/``,
``scripts/`` and ``roster.yaml`` are inside the fence, not just ``data/``) plus
every file under the gitignored ``data/session_state/`` (where step markers and
prompt files live), and ``assert_data_tree_unchanged`` refuses if a dispatch
round changed any of it. Other ignored paths (``.venv``, ``node_modules``,
``data/cache``) are large and irrelevant, and stay outside.

The snapshot lives under the git dir, outside the checkout, so the guard can
never observe itself. A missing snapshot raises: an unevaluated guard is not a
passed guard. A successful check deletes its snapshot, so a later end call with
no fresh begin raises instead of comparing against a stale one.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GUARD_DIRNAME = "midas-dispatch-guard"
_SESSION_STATE = Path("data") / "session_state"
_DELETED = "deleted"


class DispatchWroteDataError(RuntimeError):
    """A dispatch round changed files in the repository checkout."""


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True
    ).stdout


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot_path(round_name: str, root: Path) -> Path:
    git_dir = Path(_git(root, "rev-parse", "--absolute-git-dir").decode().strip())
    return git_dir / _GUARD_DIRNAME / f"{round_name}.json"


def _fingerprint(root: Path, rel: str) -> str:
    path = root / rel
    if path.is_symlink():
        return _sha(b"symlink:" + os.readlink(path).encode())
    if path.is_file():
        return _sha(path.read_bytes())
    return _DELETED


def _status_paths(root: Path) -> list[str]:
    """Every path ``git status`` lists, from a single ``-z`` call."""
    fields = _git(root, "status", "--porcelain", "--untracked-files=all", "-z").decode()
    entries = fields.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if not entry:
            continue
        paths.append(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:
            i += 1  # a rename/copy is followed by its origin path
    return paths


def _capture(root: Path) -> dict[str, str]:
    rels = set(_status_paths(root))
    state_dir = root / _SESSION_STATE
    if state_dir.is_dir():
        rels.update(
            p.relative_to(root).as_posix()
            for p in state_dir.rglob("*")
            if p.is_file() or p.is_symlink()
        )
    return {rel: _fingerprint(root, rel) for rel in sorted(rels)}


def snapshot_data_tree(round_name: str, repo_root: Path | None = None) -> None:
    """Record the state of the checkout before a dispatch round."""
    root = repo_root or _REPO_ROOT
    snap = _capture(root)
    path = _snapshot_path(round_name, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"paths": snap}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"  dispatch guard [{round_name}]: snapshot taken ({len(snap)} paths)")


def _diff(before: dict[str, str], after: dict[str, str]) -> list[str]:
    out = [f"{p} (appeared)" for p in sorted(after.keys() - before.keys())]
    out += [f"{p} (disappeared)" for p in sorted(before.keys() - after.keys())]
    out += [
        f"{p} (changed)"
        for p in sorted(before.keys() & after.keys())
        if before[p] != after[p]
    ]
    return out


def assert_data_tree_unchanged(round_name: str, repo_root: Path | None = None) -> None:
    """Raise ``DispatchWroteDataError`` naming every path the round changed.

    On success the round's snapshot is deleted.
    """
    root = repo_root or _REPO_ROOT
    path = _snapshot_path(round_name, root)
    if not path.is_file():
        raise DispatchWroteDataError(
            f"no dispatch-guard snapshot for round {round_name!r} at {path}: "
            "the guard did not run, which is not the same as the round being clean"
        )
    before = json.loads(path.read_text(encoding="utf-8"))["paths"]
    problems = _diff(before, _capture(root))
    if problems:
        raise DispatchWroteDataError(
            f"dispatch round {round_name!r} wrote in the checkout: "
            + "; ".join(problems)
        )
    path.unlink()
    print(f"  dispatch guard [{round_name}]: checkout unchanged")
