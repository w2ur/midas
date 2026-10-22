#!/usr/bin/env python3
"""Assert the day's published leaderboard is one leaderboard, not two.

A weekday session publishes the ranked rows twice: inside the daily bundle
(`data/output/<date>.json`, key `leaderboard`, which the archive pages read) and
in `data/leaderboard/current.json` (which the homepage widget reads). Both are
written from the rows one `step_build_leaderboard` call returns, so on a correct
session they are byte-for-byte the same list.

They came apart on 2026-10-13 (#116): the rows were built at Step 5, before
Step 9 refreshed the baselines the board ranks on (`vs_benchmark_pp`), and the
session rebuilt them after Step 9 for `current.json` only. The bundle ranked one
agent first, the live board another, on the same day.

Compares the newest bundle against `current.json`, both as read from the checkout
at the session commit, row for row: same agents, same order, same numbers.

Only a session run on the prompt that refreshes baselines first (Step 4c) can
be held to this. That prompt lives outside the repo and is re-pasted by hand, so
until then sessions run the old order and routinely differ (7 of the 12 sessions
before 2026-10-22). The new prompt's Step 9b therefore runs this script with
`--stamp`, which records `STAMP_KEY` in the bundle before comparing. A bundle
without the stamp was written by the old prompt: the check prints a notice and
exits 0.

Exit 0 when equal (or when the bundle carries no stamp), 1 when they differ
(naming the first difference), 2 when it could not run (a file missing or
unreadable): unknown, never healthy.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: Bundle key the new prompt's Step 9b writes. Its presence says "this session
#: refreshed the baselines before building the board", so the check applies.
STAMP_KEY = "baselines_before_board"

_BUNDLE_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


def newest_bundle(output_dir: Path) -> Path | None:
    """The bundle with the latest date in its file name, or None if there is none."""
    bundles = sorted(
        p for p in output_dir.glob("*.json") if _BUNDLE_NAME.match(p.name)
    )
    return bundles[-1] if bundles else None


def _rows(path: Path, key: str) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get(key) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"{path} carries no '{key}' list")
    return rows


def stamp_bundle(path: Path) -> None:
    """Record on the bundle that the session ran the baselines-first prompt."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    data[STAMP_KEY] = True
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def is_stamped(path: Path) -> bool:
    data = json.loads(path.read_text(encoding="utf-8"))
    return isinstance(data, dict) and data.get(STAMP_KEY) is True


def first_difference(bundle_rows: list[dict], current_rows: list[dict]) -> str | None:
    """A sentence naming the first place the two lists differ, or None if equal."""
    if len(bundle_rows) != len(current_rows):
        return (
            f"the bundle has {len(bundle_rows)} rows, current.json has "
            f"{len(current_rows)}"
        )
    for position, (b, c) in enumerate(zip(bundle_rows, current_rows), start=1):
        if b != c:
            return (
                f"row {position} differs: bundle {b.get('agent')!r} "
                f"(return_pct {b.get('return_pct')}, vs_benchmark_pp "
                f"{b.get('vs_benchmark_pp')}) against current.json "
                f"{c.get('agent')!r} (return_pct {c.get('return_pct')}, "
                f"vs_benchmark_pp {c.get('vs_benchmark_pp')})"
            )
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root holding data/ (default: the repo this lives in)",
    )
    parser.add_argument(
        "--stamp",
        action="store_true",
        help="mark the newest bundle as written by the baselines-first prompt, then compare",
    )
    args = parser.parse_args()

    bundle = newest_bundle(args.root / "data" / "output")
    current = args.root / "data" / "leaderboard" / "current.json"
    if bundle is None:
        print("No daily bundle under data/output/ to compare.", file=sys.stderr)
        return 2
    try:
        if args.stamp:
            stamp_bundle(bundle)
        elif not is_stamped(bundle):
            print(
                f"{bundle.name} carries no '{STAMP_KEY}' stamp: it was written by "
                "the pre-4c prompt order, so the one-board check is skipped.",
                file=sys.stderr,
            )
            return 0
        bundle_rows = _rows(bundle, "leaderboard")
        current_rows = _rows(current, "rows")
    except (OSError, ValueError) as exc:
        print(f"Could not read the leaderboard: {exc}", file=sys.stderr)
        return 2

    difference = first_difference(bundle_rows, current_rows)
    if difference:
        print(
            f"{bundle.name} and data/leaderboard/current.json publish different "
            f"leaderboards: {difference}.",
            file=sys.stderr,
        )
        print(
            "The rows must be built once, after Step 4c refreshed the baselines, "
            "and reused by Step 7 and Step 9b.",
            file=sys.stderr,
        )
        return 1

    print(f"{bundle.name} and current.json carry the same {len(bundle_rows)} rows.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
