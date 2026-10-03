"""Re-runnable normalisation of the OHLCV store to canonical form.

Canonical form: every ``data/market/ohlcv/{SYMBOL}.jsonl`` holds its rows in
ascending date order, one row per date. The writers in ``engine.ohlcv_ingest``
keep that form; this script brings history into it. It is a pure reorder:
**lines are moved, never re-serialised**, so every row keeps its exact bytes and
the ``{date: row}`` mapping of each file is identical before and after
(``scripts/verify_store_canonical.py`` proves it against a git ref).

Nightly fetches keep appending to main while a branch is open, so the data
commit is regenerated at merge time by re-running this script, not rebased.
It is idempotent: a canonical store is left untouched (zero files rewritten).

A file with an unparseable line, or two *different* rows for one date, is
reported and left alone (exit 1): choosing a winner is a human decision.
Two byte-identical rows for a date collapse to one.

Never commits or pushes. Dry-run by default; pass ``--apply`` to write.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from engine.ohlcv_ingest import _write_store_sorted  # noqa: E402


def canonical_form(path: Path) -> tuple[dict[str, str] | None, str]:
    """``({date: line}, "")`` for a normalisable file, or ``(None, reason)``."""
    stored: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                d = json.loads(line).get("date")
            except (json.JSONDecodeError, AttributeError):
                return None, f"line {n} is unparseable"
            if not isinstance(d, str) or not d:
                return None, f"line {n} has no date"
            if d in stored and stored[d] != line:
                return None, f"two different rows for {d}"
            stored[d] = line
    return stored, ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", type=Path, default=_PROJECT_ROOT / "data" / "market" / "ohlcv")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = ap.parse_args(argv)

    changed = refused = 0
    files = sorted(args.dir.glob("*.jsonl"))
    for path in files:
        stored, reason = canonical_form(path)
        if stored is None:
            refused += 1
            print(f"REFUSED {path.name}: {reason}", file=sys.stderr)
            continue
        want = "".join(stored[d] + "\n" for d in sorted(stored))
        if path.read_text(encoding="utf-8") == want:
            continue
        changed += 1
        if args.apply:
            _write_store_sorted(path, stored)
    verb = "rewrote" if args.apply else "would rewrite"
    print(f"{verb} {changed} of {len(files)} files; {refused} refused")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
