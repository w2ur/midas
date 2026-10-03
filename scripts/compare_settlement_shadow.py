#!/usr/bin/env python3
"""Compare one night's settlement-shadow report with what the real run did.

Stage 2.2 of the market-data structural plan. The shadow job
(`fetch_ohlcv.py --settlement-shadow`, run against the store the night STARTED
from) records what a rolling-window refetch WOULD insert, revise or refuse. The
real run's own commits (the nightly append plus the store-gap heal) are the
baseline. This answers the plan's three questions:

  1. Does every row the current pipeline inserted also appear in the shadow's
     inserts?                                  -> `missing_from_shadow`
  2. Does the shadow insert anything the current pipeline did not?
                                               -> `extra_in_shadow`
  3. How many tripwire hits does the wider window ADD over what the real run
     already quarantined?                      -> `tripwire_added`
     (`tripwire_hits` is the shadow's absolute count, kept for context.)

**`missing_from_shadow` non-empty is the plan's stop condition** for a money-tier
(held) symbol: a gap the heal pass recovered and the window did not. The script
exits 1 on ANY in-window miss (the safe side) and lists the held ones separately
as `missing_held`, from the portfolios in the checkout it runs in, so the human
judging the stop condition does not cross-reference by hand. A real insert the
real run explained from the vendor's action calendar (it sits in BOTH quarantines)
was handled, not missed, and is reported as `adjudicated_by_real_run`. Rows older than the window are reported separately as `outside_window`;
the window cannot be asked for them, and that is not a miss. Rows dated after
the shadow's `end` are `after_shadow_end`, a separate bucket.

Usage:
    python scripts/compare_settlement_shadow.py SHADOW.json --base <sha> --head <sha>

`--base` defaults to the `base_sha` the shadow recorded (the run's `github.sha`);
`--head` is the main the run left behind. Rows dated after the shadow's `end`
(a later close run's today-dated bars) are out of its reach and never a miss, and
symbols the shadow skipped for having no store (first ingest that night) are
reported as `skipped_first_ingest`, and symbols whose shadow fetch failed as
`shadow_fetch_failed`; neither is a miss (the window was never exercised). Read-only; exits 1 on a
non-empty `missing_from_shadow`, 2 when it could not form a view: unreadable
report (or one whose shape the comparison cannot process), an unimportable
fetch module, an empty git range, a shadow that served no symbol, or one whose fetch failed
for more than `fetch_ohlcv.MAX_FAILURE_RATE` of the symbols it asked about (an
UNKNOWN night is not a sample, whatever artifact it uploaded).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Run as `python scripts/compare_settlement_shadow.py`, sys.path[0] is `scripts/`,
# not the repo root, so `from scripts.fetch_ohlcv import ...` would always fail.
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
STORE_PREFIX = "data/market/ohlcv/"
QUARANTINE_PREFIX = "data/market/quarantine/"

_FILE = re.compile(r"^\+\+\+ b/" + re.escape(STORE_PREFIX) + r"(.+)\.jsonl$")
_QFILE = re.compile(r"^\+\+\+ b/" + re.escape(QUARANTINE_PREFIX) + r"(.+)\.jsonl$")
_DATE = re.compile(r'"date"\s*:\s*"(\d{4}-\d{2}-\d{2})"')


def parse_quarantined(diff: str) -> set[tuple[str, str]]:
    """{(symbol, date)} the real run appended to `data/market/quarantine/`."""
    added, _removed = _parse(diff, _QFILE)
    return added


def parse_diff(diff: str) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """(inserted, revised) as {(symbol, date)} from a `git diff -U0` of the store.

    A date added with no matching removal is an insert; a date both removed and
    added is a revision. Order-only moves of an identical line cancel out.
    """
    added, removed = _parse(diff, _FILE)
    return added - removed, added & removed


def _parse(diff: str, file_re: re.Pattern[str]) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    added: set[tuple[str, str]] = set()
    removed: set[tuple[str, str]] = set()
    symbol: str | None = None
    for line in diff.splitlines():
        m = file_re.match(line)
        if m:
            symbol = m.group(1)
            continue
        if line.startswith("--- ") or line.startswith("+++ "):
            if line.startswith("+++ ") and not m:
                symbol = None
            continue
        if symbol is None or not line or line[0] not in "+-":
            continue
        d = _DATE.search(line)
        if not d:
            continue
        (added if line[0] == "+" else removed).add((symbol, d.group(1)))
    return added, removed


def compare(
    real_inserts: set[tuple[str, str]],
    shadow: dict,
    real_quarantined: set[tuple[str, str]] | None = None,
    held: set[str] | None = None,
) -> dict:
    """Pure comparison of the real run's inserts against a shadow report.

    A real row is a miss only if the shadow COULD have produced it: dated inside
    `[window_start, end]` and for a symbol the shadow had a store file for. A
    symbol first ingested that night is skipped by design (a different code
    path the window does not change), and a row dated after the shadow's `end`
    came from a later run the shadow never asked about.
    """
    window_start = shadow["window_start"]
    end = shadow.get("end")
    skipped = set(shadow.get("skipped_no_store", []))
    # A symbol whose shadow fetch failed was never asked about: the window logic
    # was not exercised for it, so its rows are neither misses nor in scope.
    failed = set(shadow.get("failed", []))
    unreached = skipped | failed
    shadow_inserts = {(s, d) for s, ds in shadow.get("inserts", {}).items() for d in ds}
    in_window = {
        p
        for p in real_inserts
        if p[1] >= window_start and (end is None or p[1] <= end) and p[0] not in unreached
    }
    shadow_q = {(q.get("symbol"), q.get("date")) for q in shadow.get("quarantined", [])}
    real_q = real_quarantined or set()
    # The real run's `_adjudicate` re-merges an explained split row with the
    # tripwire off, so it is both quarantined AND inserted; the shadow (tripwire
    # on) refuses the same row. Handled on both sides, not a gap.
    adjudicated = (in_window - shadow_inserts) & shadow_q & real_q
    missing = sorted(in_window - shadow_inserts - adjudicated)
    return {
        "window_start": window_start,
        "end": end,
        "base_sha": shadow.get("base_sha"),
        "run_id": shadow.get("run_id"),
        "real_inserts": len(real_inserts),
        "shadow_inserts": len(shadow_inserts),
        "missing_from_shadow": missing,
        "missing_held": sorted(p for p in missing if p[0] in (held or set())),
        "adjudicated_by_real_run": sorted(adjudicated),
        "skipped_first_ingest": sorted(p for p in real_inserts if p[0] in skipped),
        "shadow_fetch_failed": sorted(p for p in real_inserts if p[0] in failed and p[0] not in skipped),
        # Older than the window: what the gap pass reaches and the window cannot.
        "outside_window": sorted(
            p for p in real_inserts - in_window if p[0] not in unreached and p[1] < window_start
        ),
        # Dated after the shadow's `end`: a later close run's bars, a different
        # question from a pre-window heal and never mixed into that bucket.
        "after_shadow_end": sorted(
            p
            for p in real_inserts - in_window
            if p[0] not in unreached and end is not None and p[1] > end
        ),
        "extra_in_shadow": sorted(shadow_inserts - real_inserts),
        "tripwire_hits": len(shadow_q),
        "real_quarantined": len(real_q),
        "tripwire_added": len(shadow_q - real_q),
        "shadow_revisions": sum(len(v) for v in shadow.get("revisions", {}).values()),
    }


def failure_rate(shadow: dict) -> float:
    """Share of the symbols the shadow asked the vendor about that returned nothing."""
    failed = len(shadow.get("failed", []))
    asked = int(shadow.get("served") or 0) + failed
    return failed / asked if asked else 0.0


def _git_diff(base: str, head: str) -> str:
    return subprocess.run(
        ["git", "-C", str(_PROJECT_ROOT), "diff", "-U0", base, head, "--", STORE_PREFIX, QUARANTINE_PREFIX],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _held() -> set[str]:
    try:
        from scripts.fetch_ohlcv import _collect_holdings

        return _collect_holdings()
    except Exception as exc:  # noqa: BLE001 - holdings are context, never a reason to hide a miss
        print(f"warning: holdings unreadable, missing_held will be empty: {exc}", file=sys.stderr)
        return set()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shadow", type=Path)
    parser.add_argument("--base", help="default: the base_sha recorded in the shadow report")
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    try:
        shadow = json.loads(args.shadow.read_text(encoding="utf-8"))
        shadow["window_start"], shadow["inserts"]  # noqa: B018 - shape check
    except (OSError, ValueError, KeyError) as exc:
        print(f"cannot read the shadow report: {exc}", file=sys.stderr)
        return 2
    if not shadow.get("served"):
        print("the shadow served no symbol: UNKNOWN night, not a sample", file=sys.stderr)
        return 2
    try:
        from scripts.fetch_ohlcv import MAX_FAILURE_RATE

        rate = failure_rate(shadow)
    except Exception as exc:  # noqa: BLE001 - could not form a view is UNKNOWN (2), never the stop condition (1)
        print(f"could not evaluate the shadow's failure rate: {exc!r}: UNKNOWN night", file=sys.stderr)
        return 2
    if rate > MAX_FAILURE_RATE:
        # Same limit as the real fetch: a brown-out night exercised the window
        # for a sliver of the universe and every failed symbol's rows leave scope.
        print(
            f"the shadow's fetch failed for {rate:.0%} of the symbols it asked about "
            f"(limit {MAX_FAILURE_RATE:.0%}): UNKNOWN night, not a sample",
            file=sys.stderr,
        )
        return 2
    base = args.base or shadow.get("base_sha")
    if not base:
        print("no --base and the report records no base_sha", file=sys.stderr)
        return 2
    try:
        diff = _git_diff(base, args.head)
        inserted, _revised = parse_diff(diff)
        quarantined = parse_quarantined(diff)
    except subprocess.CalledProcessError as exc:
        print(f"git diff failed: {exc.stderr}", file=sys.stderr)
        return 2
    if not inserted:
        # Every full-universe night appends at least the `end` row for hundreds
        # of symbols: an empty insert set means a wrong --base/--head (or a head
        # taken before the night's commit), not a clean night.
        print("the git range inserted no store row: UNKNOWN night, check --base/--head", file=sys.stderr)
        return 2
    try:
        report = compare(inserted, shadow, quarantined, held=_held())
        rendered = json.dumps(report, indent=1)
    except Exception as exc:  # noqa: BLE001 - a malformed report is UNKNOWN (2), never the stop condition (1)
        print(f"could not compare the shadow report: {exc!r}: UNKNOWN night", file=sys.stderr)
        return 2
    print(rendered)
    return 1 if report["missing_from_shadow"] else 0


if __name__ == "__main__":
    sys.exit(main())
