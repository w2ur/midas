#!/usr/bin/env python3
"""CI gate: published dated rows are append-only.

    python scripts/check_append_only.py --base <sha> --head <sha>

Exit 0 when every dated row that existed in ``base`` is byte-identical in
``head``, or when the change is explicitly declared a restatement. Exit 1
otherwise, naming the rows.

## Why a CI gate and not just the application code

Immutability of the published record is already enforced where rows are
written: `PortfolioManager.add_snapshot` refuses a later session's rewrite of
an earlier session's row, and `engine.baselines.merge_baseline_series` is
append-or-refuse. Both are real and both are tested.

Neither sees a hand edit, a one-off script, a bad merge resolution, or a
future writer that forgets to go through them. Every published-data incident
in this project's history arrived by one of those routes — the 2026-08-03
snapshot overwrite, the retroactive baseline drift, the blanket `restate=True`
that moved eight benchmark series it should not have, and the 2026-08-02
ledger rewrite that went unlogged for five days. The last of those was caught
"by a cross-check on a different task, not by any process", in the
methodology's own words. This is the process.

## The one legitimate rewrite

A session correcting **its own** row. That is precisely the case
`add_snapshot` allows (a resumed or re-run session must be able to fix what it
just wrote) and precisely the case it distinguishes by `session_date`. This
gate applies the same rule rather than a stricter one, because a gate that
forbids something the application deliberately permits is a gate that will be
switched off.

Baseline series carry no `session_date` — they are derived from the price
series, so there is no "same writer" to appeal to. Any change to a published
baseline row is a restatement, which is the conclusion the 2026-08-07
coin-flip work reached the hard way.

## Declaring a restatement

Put `[restate]` in the commit message. That is not a bypass — it is the
disclosure requirement made mechanical: a restatement that has to be declared
in the commit subject is a restatement somebody has to think about, and
`git log --grep='\\[restate\\]'` is then a complete list of every time the
published record moved.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass

#: Trees whose dated rows are published and therefore frozen.
WATCHED_GLOBS = (
    "data/portfolios/*/snapshots.json",
    "data/baselines/*/*.json",
)

RESTATE_TRAILER = "[restate]"


@dataclass(frozen=True)
class Violation:
    path: str
    row_date: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}  {self.row_date}  {self.detail}"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def _changed_files(base: str, head: str) -> list[str]:
    out = _git("diff", "--name-only", "--diff-filter=M", f"{base}..{head}")
    paths = [line.strip() for line in out.splitlines() if line.strip()]
    return [p for p in paths if _is_watched(p)]


def _is_watched(path: str) -> bool:
    from fnmatch import fnmatch

    return any(fnmatch(path, pattern) for pattern in WATCHED_GLOBS)


def _rows_at(ref: str, path: str) -> dict[str, dict] | None:
    """`{date: row}` for a JSON array of dated rows, or None if unreadable.

    Unreadable is not a violation: a file that did not exist at `base`, or
    that holds something other than an array of dated dicts, is outside what
    this gate can speak to. It reports nothing rather than guessing — the
    alternative is a gate that fails on shapes it does not understand, which
    is how gates get disabled.
    """
    try:
        raw = _git("show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    rows: dict[str, dict] = {}
    for row in parsed:
        if isinstance(row, dict) and isinstance(row.get("date"), str):
            rows[row["date"]] = row
    return rows


#: Only agent snapshots have a writer identity to appeal to. The exemption is
#: scoped to this filename rather than to "any row carrying a session_date",
#: so a stray field appearing in a derived series can never buy it an excuse.
_SESSION_KEYED = "snapshots.json"


def _same_session_correction(path: str, old: dict, new: dict) -> bool:
    """True when one session is correcting a row it wrote itself.

    Mirrors `PortfolioManager.add_snapshot`. A row with no `session_date` is
    legacy and fails closed — the same choice `add_snapshot` makes, and for
    the same reason: an unknown writer cannot be shown to be the same writer.

    Baseline series get no exemption at all. They are derived from the price
    series, so there is no "same writer" — a moved baseline point is a revised
    close reaching back under a frozen agent curve, which is the exact
    asymmetry `engine.baselines.merge_baseline_series` was made append-or-refuse
    to close on 2026-08-06.
    """
    if not path.endswith(_SESSION_KEYED):
        return False
    old_session = old.get("session_date")
    new_session = new.get("session_date")
    return bool(old_session) and old_session == new_session


def find_violations(base: str, head: str) -> list[Violation]:
    violations: list[Violation] = []
    for path in _changed_files(base, head):
        before = _rows_at(base, path)
        after = _rows_at(head, path)
        if before is None or after is None:
            continue
        for row_date, old_row in before.items():
            new_row = after.get(row_date)
            if new_row is None:
                violations.append(Violation(path, row_date, "row deleted"))
                continue
            if new_row == old_row:
                continue
            if _same_session_correction(path, old_row, new_row):
                continue
            changed = sorted(
                key
                for key in set(old_row) | set(new_row)
                if old_row.get(key) != new_row.get(key)
            )
            # Reason states *what* changed, never whether it was declared —
            # the caller knows that and says so in its header. Baking
            # "without [restate]" in here printed it on every row of a
            # correctly *declared* restatement too, directly under a header
            # saying the opposite. An honesty tool contradicting itself in
            # its own output is worse than terse output.
            violations.append(Violation(path, row_date, f"changed {changed}"))
    return violations


def _commit_messages(base: str, head: str) -> str:
    return _git("log", "--format=%B", f"{base}..{head}")


def _resolves(ref: str) -> bool:
    try:
        _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    except subprocess.CalledProcessError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="ref the rows are frozen from")
    parser.add_argument("--head", required=True, help="ref being checked")
    args = parser.parse_args()

    # "Cannot evaluate" must never look like "violation". Both used to exit 1:
    # on a shallow checkout `HEAD^` does not resolve, git raised, the traceback
    # exited 1, and the caller read that as a rewritten published row. A gate
    # whose crash is indistinguishable from its finding is a false-alarm
    # generator, and false alarms are how gates get switched off.
    if not _resolves(args.head):
        print(f"::error::--head {args.head!r} does not resolve to a commit.")
        return 2
    if not _resolves(args.base):
        # The genuine case: the repository's first commit, or a checkout too
        # shallow to have a parent. There are no frozen rows to protect yet.
        print(
            f"append-only: --base {args.base!r} does not resolve "
            "(shallow checkout or root commit) — nothing to compare against."
        )
        return 0

    try:
        declared = RESTATE_TRAILER in _commit_messages(args.base, args.head)
        violations = find_violations(args.base, args.head)
    except subprocess.CalledProcessError as exc:
        print(f"::error::append-only check could not run: {exc}")
        return 2

    if not violations:
        print("append-only: no published row was modified.")
        return 0

    if declared:
        print(
            f"append-only: {len(violations)} published row(s) modified, declared "
            f"{RESTATE_TRAILER} in the commit message:"
        )
        for violation in violations:
            print(f"  {violation}")
        print(
            "Reminder: a restatement needs a METHODOLOGY changelog entry. "
            "See scripts/restate_valuations.py --changelog-entry."
        )
        return 0

    print(f"::error::{len(violations)} published dated row(s) were modified:")
    for violation in violations:
        print(f"  {violation}")
    print(
        "\nPublished rows are append-only. A session may correct its own row "
        "(same session_date); anything else is a restatement and must say so:\n"
        f"  - put {RESTATE_TRAILER} in the commit message, and\n"
        "  - ship a METHODOLOGY changelog entry describing what moved and why."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
