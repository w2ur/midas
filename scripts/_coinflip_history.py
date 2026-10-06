"""Git and tree helpers shared by the two coin-flip history tools.

``scripts/audit_coinflip_seams.py`` (read-only: measures the splice seams in
the published coin flips) and ``scripts/restate_coinflip.py`` (the one gated
restatement of them) both read the history of
``data/baselines/<agent>/coinflip.json`` and both run a writer's inputs from
that writer's parent tree, ``<writer>^``: the tree the session that wrote a row
read. They do **not** attribute a row to the same writer, and that is
deliberate; the two rules are exposed here by name so neither tool can pick up
the other's by accident:

- ``first_writer`` — the first commit that wrote a row. ``restate_coinflip``
  uses it: a restated row must be drawn from the universe the desk had when
  the row was first published. A later commit that rewrote the row (a
  restatement) never picks the universe, or a universe chosen later would
  decide earlier picks — look-ahead.
- ``last_writer`` — the last commit that set a row's value. The seam audit
  uses it: its control recomputes a published row with its writer's inputs
  and must reproduce it exactly, and the value published at the tip is the
  one the last writer computed.

Live-only, like both tools: neither is in ``scripts/sync_core.py``'s
``CORE_SCRIPTS``, so this module ships nowhere either, and its tests live in
the two tools' test files (both in ``LIVE_ONLY_TESTS``).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path


def git(repo: Path, *args: str, binary: bool = False):
    """``git <args>`` in ``repo``: its stdout, decoded unless ``binary``.
    Raises ``CalledProcessError`` on a non-zero exit."""
    out = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True).stdout
    return out if binary else out.decode()


def rev(repo: Path, spec: str) -> str | None:
    """The object ``spec`` names (``<rev>`` or ``<rev>:<path>``), or None."""
    try:
        return git(repo, "rev-parse", "--verify", "-q", spec).strip() or None
    except subprocess.CalledProcessError:
        return None


def exists(repo: Path, at: str, path: str) -> bool:
    """Whether ``at``'s tree holds ``path``."""
    try:
        git(repo, "cat-file", "-e", f"{at}:{path}")
        return True
    except subprocess.CalledProcessError:
        return False


def is_ancestor(repo: Path, ancestor: str, of: str) -> bool:
    """Whether ``ancestor`` is ``of`` or in its history. False when
    ``ancestor`` names no commit of ``repo``."""
    r = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, of], cwd=repo, capture_output=True
    )
    if r.returncode not in (0, 1) and rev(repo, f"{ancestor}^{{commit}}") is not None:
        raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)
    return r.returncode == 0


def extract(repo: Path, at: str, paths: list[str], target: Path) -> None:
    """Write ``paths`` of ``at``'s tree under ``target`` (``git archive``)."""
    archive = git(repo, "archive", at, *paths, binary=True)
    subprocess.run(["tar", "-x", "-C", str(target)], input=archive, check=True)


def _versions(repo: Path, tip: str, relpath: str) -> Iterator[tuple[str, list[dict]]]:
    """``(sha, rows)`` for every commit up to ``tip`` that touched ``relpath``,
    oldest first. A commit where the file is deleted or unreadable wrote
    nothing and is skipped."""
    commits = git(repo, "log", "--format=%H", "--reverse", tip, "--", relpath).split()
    for c in commits:
        try:
            rows = json.loads(git(repo, "show", f"{c}:{relpath}"))
        except (subprocess.CalledProcessError, ValueError):
            continue
        yield c, rows


def first_writer(repo: Path, tip: str, relpath: str) -> dict[str, str]:
    """``{date: sha}`` of the first commit, up to ``tip``, whose ``relpath``
    holds a row for that date. Used by ``restate_coinflip`` (see the module
    docstring): the inputs a row was first published under."""
    first: dict[str, str] = {}
    for c, rows in _versions(repo, tip, relpath):
        for r in rows:
            first.setdefault(r["date"], c)
    return first


def last_writer(repo: Path, tip: str, relpath: str) -> dict[str, str]:
    """``{date: sha}`` of the last commit, up to ``tip``, that set the value
    of that date's row: one that changed its ``portfolio_value``, or wrote it
    where the previous version held no such row. Used by the seam audit (see
    the module docstring): the writer whose computation the tip publishes."""
    current: dict[str, float] = {}
    writer: dict[str, str] = {}
    for c, rows in _versions(repo, tip, relpath):
        values = {r["date"]: r["portfolio_value"] for r in rows}
        for d, v in values.items():
            if current.get(d) != v:
                writer[d] = c
        current = values
    return writer
