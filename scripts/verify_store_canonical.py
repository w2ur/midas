"""Verify the OHLCV store is canonical AND value-neutral against a git ref.

Checks, over every ``data/market/ohlcv/*.jsonl``:

1. canonical: dates ascending, no duplicate date, no unparseable line;
2. value-neutral: the ``{date: parsed row}`` mapping equals the one at ``--against``
   (same file set, same dates, same values), and the raw line multiset is
   identical too (nothing re-serialised);
3. ``scripts.fetch_market_data._newest_stored_date`` (which reads the tail)
   returns the file's maximum date.

Read-only. Exit 0 = all hold, 1 = a violation (each printed), 2 = could not run.
Files added after the ref (e.g. by a later nightly) are reported as `new` and
checked for canonical form only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.fetch_market_data import _newest_stored_date  # noqa: E402

REL = "data/market/ohlcv"


def _git(*args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(_PROJECT_ROOT), *args], check=True, capture_output=True
    ).stdout


def _parse(text: str) -> tuple[list[str], list[str], dict[str, dict]]:
    dates: list[str] = []
    lines: list[str] = []
    rows: dict[str, dict] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        row = json.loads(line)  # raises on an unparseable line: that is a violation
        dates.append(row["date"])
        lines.append(line)
        rows[row["date"]] = row
    return dates, lines, rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--against", required=True, help="git ref holding the pre-normalisation store")
    ap.add_argument("--dir", type=Path, default=_PROJECT_ROOT / REL)
    args = ap.parse_args(argv)

    try:
        ref_files = set(_git("ls-tree", "-r", "--name-only", args.against, "--", REL).decode().split("\n")) - {""}
    except subprocess.CalledProcessError as e:
        print(f"cannot read {args.against}: {e}", file=sys.stderr)
        return 2
    if not ref_files:
        print(f"no store files at {args.against}: unknown, not healthy", file=sys.stderr)
        return 2

    problems: list[str] = []
    now_files = sorted(args.dir.glob("*.jsonl"))
    now_names = {f"{REL}/{p.name}" for p in now_files}
    ref_jsonl = {f for f in ref_files if f.endswith(".jsonl")}
    for missing in sorted(ref_jsonl - now_names):
        problems.append(f"{missing}: file vanished")

    stats = dict(files=0, compared=0, new=0, was_unsorted=0, tail_ne_max_before=0, tail_ne_max_after=0)
    for path in now_files:
        stats["files"] += 1
        rel = f"{REL}/{path.name}"
        try:
            dates, lines, rows = _parse(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError) as e:
            problems.append(f"{rel}: unreadable after ({e})")
            continue
        if dates != sorted(dates) or len(set(dates)) != len(dates):
            problems.append(f"{rel}: not canonical (unsorted or duplicate dates)")
        newest = _newest_stored_date(path)
        if dates and newest != max(dates):
            stats["tail_ne_max_after"] += 1
            problems.append(f"{rel}: _newest_stored_date={newest} != max {max(dates)}")
        if rel not in ref_jsonl:
            stats["new"] += 1
            continue
        stats["compared"] += 1
        b_dates, b_lines, b_rows = _parse(_git("show", f"{args.against}:{rel}").decode("utf-8"))
        if b_dates != sorted(b_dates):
            stats["was_unsorted"] += 1
        if b_dates and b_dates[-1] != max(b_dates):
            stats["tail_ne_max_before"] += 1
        if rows != b_rows:
            problems.append(f"{rel}: {{date: row}} mapping differs from {args.against}")
        # Sets, not multisets: the normaliser collapses a byte-identical repeat.
        if set(lines) != set(b_lines):
            problems.append(f"{rel}: raw lines differ from {args.against} (re-serialised?)")

    print(json.dumps(stats))
    for p in problems:
        print(f"VIOLATION {p}", file=sys.stderr)
    print("OK" if not problems else f"{len(problems)} violation(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
