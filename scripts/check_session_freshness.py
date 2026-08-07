#!/usr/bin/env python3
"""Assert Step 9 ran by reading published state, not the commit's diff.

`session-integrity` (and the inline copy in `auto-merge-session`) used to
answer "did Step 9 refresh the baselines?" by grepping the changed-file list
for `^data/baselines/`. That proxy holds only while every Step 9 writes
something, and `merge_baseline_series` is append-or-refuse — so a session whose
series was already rebuilt earlier the same day writes nothing and looks
identical to a session that skipped the step. On 2026-08-07 commit
`a4dc9dce2 [restate]` did exactly that, and both guards failed a correct
session.

The invariant that actually distinguishes the two: Step 9 builds the baseline
series alongside the agent snapshots, so a *skip* leaves the baselines behind
the snapshots — the Apr 25 shape the guard was built for — while a correct
no-op leaves them level. A baseline may legitimately run *ahead* of a book (a
restatement can publish a point the book has not reached), so this is a
one-sided check.

Exit 0 when every series is current, 1 when any lags, naming the agents.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Baseline series written per agent by Step 9.
BASELINE_KINDS = ("benchmark", "coinflip")

#: The cross-agent reference series, keyed on the desk's newest snapshot.
GLOBAL_SERIES = Path("global") / "msci_world.json"


def _max_date(path: Path) -> str | None:
    """Newest `date` in a snapshot/baseline series file, or None if unusable.

    Unreadable is deliberately not the same as absent: a corrupt series must
    not read as "current" just because it fails to parse.
    """
    if not path.exists():
        return None
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    dates = [r["date"] for r in rows if isinstance(r, dict) and r.get("date")]
    return max(dates) if dates else None


def stale_baselines(root: Path) -> list[str]:
    """Agents whose baseline series has not caught up with their snapshots.

    Returns agent ids in sorted order, with `global` last when the shared
    reference series lags the newest snapshot on the desk.
    """
    portfolios = root / "data" / "portfolios"
    baselines = root / "data" / "baselines"

    stale: list[str] = []
    desk_newest: str | None = None

    for agent_dir in sorted(p for p in portfolios.glob("*") if p.is_dir()):
        agent = agent_dir.name
        snapshot_date = _max_date(agent_dir / "snapshots.json")
        if snapshot_date is None:
            # Never valued, or unreadable — no baseline claim to make.
            continue
        if not (baselines / agent).exists():
            # Books without a passive benchmark (the allocator, its baseline)
            # are not Step 9's output and carry no series to compare.
            continue

        desk_newest = max(desk_newest or snapshot_date, snapshot_date)

        series = [_max_date(baselines / agent / f"{k}.json") for k in BASELINE_KINDS]
        if any(d is None or d < snapshot_date for d in series):
            stale.append(agent)

    global_date = _max_date(baselines / GLOBAL_SERIES)
    if desk_newest is not None and (global_date is None or global_date < desk_newest):
        stale.append("global")

    return stale


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root holding data/ (default: the repo this lives in)",
    )
    args = parser.parse_args()

    stale = stale_baselines(args.root)
    if stale:
        print(
            "Baseline series lag their snapshots (Step 9 skipped): " + ", ".join(stale),
            file=sys.stderr,
        )
        print(
            "See CLAUDE.md > Session Cadence — Step 9 builds data/baselines/ "
            "alongside the agent snapshots.",
            file=sys.stderr,
        )
        return 1

    print("Baselines are current with every valued book.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
