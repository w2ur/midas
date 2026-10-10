"""A tripwire around a persona dispatch round.

Dispatched agents now hold live web tools, and what a page returns is untrusted
text that may try to make them write a file. The prompt says not to; this checks
afterwards whether one did. It is a tripwire against an agent writing files, not
containment: a subagent with Bash runs in the same sandbox as the session, so a
capable injection could act outside what is fingerprinted here, or reach the
network through its own tools. What bounds every order is the broker's rails.

``snapshot_data_tree`` records, and ``assert_data_tree_unchanged`` compares:

- every path ``git status`` lists (modified, staged, renamed, untracked — so
  ``engine/``, ``scripts/`` and ``roster.yaml`` are inside, not just ``data/``),
  by content and by its two-letter status code, so a staged change shows too;
- ``HEAD`` (commit and symbolic ref) and every ref, so a commit made during the
  round, which leaves ``git status`` clean, is still seen;
- the index entries marked skip-worktree or assume-unchanged, which hide a
  written tracked file from ``git status``;
- ``info/exclude``, ``info/attributes``, the repository config and every hook
  file, which can hide a new file or run code at the next commit;
- the gitignored inputs the session reads after a round: everything under
  ``data/session_state/`` (step markers, prompt files) and
  ``data/market/today.json`` (the prices Step 4 writes immutable snapshots
  from);
- the ``.pth`` and ``sitecustomize``/``usercustomize`` files in the running
  interpreter's site-packages, which run code at every interpreter start.

Other ignored paths (``.venv`` beyond those files, ``node_modules``,
``data/cache``) are large and stay outside.

The snapshot lives under the git dir, outside the checkout, so the guard never
observes itself. Begin returns a token, the sha256 of the snapshot it wrote; the
token stays in the orchestrator's context and end refuses a snapshot whose
digest differs, so a rewritten snapshot is not a clean round. End refuses on a
missing snapshot or token, and turns any error it meets into
``DispatchWroteDataError``: an end call that cannot evaluate is not a pass. A
successful end deletes its snapshot, so a later end with no fresh begin raises.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GUARD_DIRNAME = "midas-dispatch-guard"
_SESSION_STATE = Path("data") / "session_state"
_IGNORED_INPUTS = ("data/market/today.json",)
_GIT_FILES = ("info/exclude", "info/attributes", "config", "config.worktree")
_STARTUP_FILES = ("sitecustomize.py", "usercustomize.py")
_DELETED = "deleted"


class DispatchWroteDataError(RuntimeError):
    """A dispatch round changed what the guard watches, or could not be checked."""


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True
    ).stdout


def _git_str(root: Path, *args: str) -> str:
    return os.fsdecode(_git(root, *args)).strip()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _show(key: str) -> str:
    """A key as printable text, whatever bytes its file name held."""
    return os.fsencode(key).decode("utf-8", "backslashreplace")


def _snapshot_path(round_name: str, root: Path) -> Path:
    git_dir = Path(_git_str(root, "rev-parse", "--absolute-git-dir"))
    return git_dir / _GUARD_DIRNAME / f"{round_name}.json"


def _git_path(root: Path, name: str) -> Path:
    path = Path(_git_str(root, "rev-parse", "--git-path", name))
    return path if path.is_absolute() else root / path


def _fingerprint(path: Path) -> str:
    if path.is_symlink():
        return _sha(b"symlink:" + os.fsencode(os.readlink(path)))
    if path.is_file():
        return _sha(path.read_bytes())
    return _DELETED


def _split_z(raw: bytes) -> list[str]:
    return [os.fsdecode(field) for field in raw.split(b"\0") if field]


def _status(root: Path) -> dict[str, str]:
    """``{path: XY}`` for every path ``git status`` lists, renames on both sides."""
    entries = _split_z(
        _git(root, "status", "--porcelain", "--untracked-files=all", "-z")
    )
    out: dict[str, str] = {}
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        code, path = entry[:2], entry[3:]
        out[path] = code
        if "R" in code or "C" in code:
            out[entries[i]] = code  # a rename/copy is followed by its origin path
            i += 1
    return out


def _hidden_index_flags(root: Path) -> dict[str, str]:
    """Index entries tagged assume-unchanged (lowercase) or skip-worktree (S)."""
    out: dict[str, str] = {}
    for entry in _split_z(_git(root, "ls-files", "-v", "-z")):
        tag, path = entry[:1], entry[2:]
        if tag.islower() or tag == "S":
            out[path] = tag
    return out


def _refs(root: Path) -> dict[str, str]:
    lines = _git_str(
        root, "for-each-ref", "--format=%(refname) %(objectname)"
    ).splitlines()
    return dict(line.rsplit(" ", 1) for line in lines if line)


def _site_packages_dirs() -> list[Path]:
    return sorted(Path(sys.prefix).glob("lib/python*/site-packages"))


def _capture(root: Path) -> dict[str, str]:
    snap: dict[str, str] = {}

    status = _status(root)
    rels = set(status)
    state_dir = root / _SESSION_STATE
    if state_dir.is_dir():
        rels.update(
            p.relative_to(root).as_posix()
            for p in state_dir.rglob("*")
            if p.is_file() or p.is_symlink()
        )
    rels.update(_IGNORED_INPUTS)
    for rel in rels:
        snap[rel] = _fingerprint(root / rel)
    for rel, code in status.items():
        snap[f"index-status:{rel}"] = code

    head = _git_str(root, "rev-parse", "HEAD")
    head_ref = _git_str(root, "rev-parse", "--symbolic-full-name", "HEAD")
    snap["HEAD"] = f"{head} {head_ref}"
    for ref, sha in _refs(root).items():
        snap[f"ref:{ref}"] = sha
    for rel, tag in _hidden_index_flags(root).items():
        snap[f"index-flag:{rel}"] = tag

    for name in _GIT_FILES:
        snap[f"git:{name}"] = _fingerprint(_git_path(root, name))
    hooks = _git_path(root, "hooks")
    if hooks.is_dir():
        for hook in hooks.rglob("*"):
            if hook.is_file() or hook.is_symlink():
                snap[f"git:hooks/{hook.relative_to(hooks).as_posix()}"] = (
                    _fingerprint(hook)
                )

    for site in _site_packages_dirs():
        startup = [*site.glob("*.pth"), *(site / n for n in _STARTUP_FILES)]
        for path in startup:
            if path.exists() or path.is_symlink():
                snap[f"site-packages:{path.name}"] = _fingerprint(path)
    return snap


def snapshot_data_tree(round_name: str, repo_root: Path | None = None) -> str:
    """Record what the guard watches before a dispatch round; return the token.

    The token is the sha256 of the snapshot written. Keep it in the
    orchestrator and hand it to ``assert_data_tree_unchanged``.
    """
    root = repo_root or _REPO_ROOT
    snap = _capture(root)
    data = (json.dumps({"paths": snap}, indent=2, sort_keys=True) + "\n").encode(
        "ascii"
    )
    path = _snapshot_path(round_name, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f"  dispatch guard [{round_name}]: snapshot taken ({len(snap)} entries)")
    return _sha(data)


def _diff(before: dict[str, str], after: dict[str, str]) -> list[str]:
    out = [f"{_show(p)} (appeared)" for p in sorted(after.keys() - before.keys())]
    out += [f"{_show(p)} (disappeared)" for p in sorted(before.keys() - after.keys())]
    out += [
        f"{_show(p)} (changed)"
        for p in sorted(before.keys() & after.keys())
        if before[p] != after[p]
    ]
    return out


def _check(round_name: str, token: str | None, root: Path) -> None:
    if not token:
        raise DispatchWroteDataError(
            f"no dispatch-guard token for round {round_name!r}: pass the value "
            "step_guard_dispatch_begin returned"
        )
    path = _snapshot_path(round_name, root)
    if not path.is_file():
        raise DispatchWroteDataError(
            f"no dispatch-guard snapshot for round {round_name!r} at {path}: "
            "the guard did not run, which is not the same as the round being clean"
        )
    data = path.read_bytes()
    if _sha(data) != token:
        raise DispatchWroteDataError(
            f"dispatch-guard snapshot for round {round_name!r} at {path} does not "
            "match the token taken at begin: the snapshot was rewritten"
        )
    before = json.loads(data)["paths"]
    problems = _diff(before, _capture(root))
    if problems:
        raise DispatchWroteDataError(
            f"dispatch round {round_name!r} wrote in the checkout: "
            + "; ".join(problems)
        )
    path.unlink()


def assert_data_tree_unchanged(
    round_name: str, token: str | None, repo_root: Path | None = None
) -> None:
    """Raise ``DispatchWroteDataError`` naming everything the round changed.

    Any other error met while checking is raised as ``DispatchWroteDataError``
    too, with its cause. On success the round's snapshot is deleted.
    """
    root = repo_root or _REPO_ROOT
    try:
        _check(round_name, token, root)
    except DispatchWroteDataError:
        raise
    except Exception as exc:
        raise DispatchWroteDataError(
            f"dispatch guard for round {round_name!r} could not be evaluated, "
            f"which is not a pass: {type(exc).__name__}: {exc}"
        ) from exc
    print(f"  dispatch guard [{round_name}]: checkout unchanged")
