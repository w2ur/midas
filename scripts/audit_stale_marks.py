#!/usr/bin/env python3
"""Read-only audit: published snapshot marks priced at a close the vendor later served.

    python scripts/audit_stale_marks.py [--since 2026-08-20] [--repo .] [--json]

For every weekday-session commit since ``--since`` (subject
``chore: weekday session YYYY-MM-DD``), and every snapshot row that commit
wrote, it rebuilds what the session saw:

- the book's positions, from ``portfolio.json`` in that commit's tree (fills
  run before the snapshot step, and nothing after it trades);
- each position's ``price_date``: the newest row on or before the snapshot's
  date in ``data/market/ohlcv/<TICKER>.jsonl`` in that same tree, which is
  exactly what `engine.quotes.latest_price` read that evening.

A mark is **stale** when the store at ``HEAD`` holds a row for that ticker
dated after ``price_date`` and on or before the snapshot's date: the vendor
did serve a newer close for the date being valued, and the published row
used an older one. That is a hindsight test, and it is the right one for a
historical audit; the live check written on new rows since 2026-10-03
(`engine.stale_marks`) cannot see the future and judges against the bucket
instead.

One class is excluded from that test because it is late by design, not by the
vendor: since the 2026-09-28 cadence move a session prices the day it runs on,
and inside that row crypto, FX and futures mark at the previous completed bar
(CLAUDE.md, Session Cadence; METHODOLOGY ``#same-day-close-2026-09-28``). Hindsight
always finds that day's bar later, so a mark of that class, in a row dated on
its own session's day, priced at the newest close before the row's date, with
the only newer close being the row's own date, is reported separately as
``by_design`` and never counted stale.

Nothing is written. Published snapshots are immutable; this script is the
source of the disclosure in METHODOLOGY (``#stale-marks-2026-10-03``), not a
restatement. A row written twice (a re-run) is counted once, as last written.

Exit codes (portfolio convention): 0 no stale mark found, 1 stale marks found
(the finding), 2 could not run or found no session commit to audit (unknown,
never "healthy").
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.market_calendar import store_bucket  # noqa: E402

SESSION_SUBJECT = re.compile(r"^chore: weekday session (\d{4}-\d{2}-\d{2})\b")
SNAPSHOTS_GLOB = "data/portfolios/*/snapshots.json"
STORE = "data/market/ohlcv"


class Git:
    """One long-lived ``git cat-file --batch`` for blob reads."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self._proc = subprocess.Popen(
            ["git", "-C", str(repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def run(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def blob(self, rev: str, path: str) -> bytes | None:
        assert self._proc.stdin and self._proc.stdout
        self._proc.stdin.write(f"{rev}:{path}\n".encode())
        self._proc.stdin.flush()
        header = self._proc.stdout.readline().decode()
        if header.rstrip().endswith("missing"):
            return None
        size = int(header.split()[2])
        data = self._proc.stdout.read(size)
        self._proc.stdout.read(1)  # trailing newline
        return data

    def close(self) -> None:
        if self._proc.stdin:
            self._proc.stdin.close()
        self._proc.wait()
        if self._proc.stdout:
            self._proc.stdout.close()


def _json(raw: bytes | None) -> object:
    if raw is None:
        return None
    # Old snapshot files carry bare NaN tokens, which json accepts.
    return json.loads(raw)


def _row_dates(raw: bytes | None) -> list[tuple[str, bool]]:
    """``(date, has_close)`` per parseable store row."""
    if raw is None:
        return []
    out: list[tuple[str, bool]] = []
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        d = row.get("date")
        if isinstance(d, str):
            out.append((d, row.get("close") is not None))
    return out


def price_date_on_or_before(raw: bytes | None, on: str) -> str | None:
    """The date `engine.ohlcv_store.latest_close_on_or_before` would read."""
    best: tuple[str, bool] | None = None
    for d, has_close in _row_dates(raw):
        if d <= on and (best is None or d > best[0]):
            best = (d, has_close)
    if best is None or not best[1]:
        return None
    return best[0]


def newer_served(raw: bytes | None, after: str, on_or_before: str) -> str | None:
    """Newest date in ``(after, on_or_before]`` the store holds a close for."""
    dates = [d for d, has_close in _row_dates(raw) if has_close and after < d <= on_or_before]
    return max(dates) if dates else None


def marks_at_previous_bar(ticker: str) -> bool:
    """Crypto, FX and futures: marked at the previous completed bar by design."""
    return ticker.endswith("=F") or store_bucket(ticker) in ("crypto", "fx")


def _day_before(d: str) -> str:
    return (date.fromisoformat(d) - timedelta(days=1)).isoformat()


def is_by_design(
    ticker: str, snapshot_date: str, session_date: str,
    price_date: str | None, raw_head: bytes | None,
) -> bool:
    """A previous-completed-bar mark the cadence intends, not a vendor hole.

    The row is dated on its own session's day (the post-2026-09-28 cadence),
    the ticker is of a class marked at the previous completed bar, and hindsight
    holds no close strictly between ``price_date`` and the row's date: the one
    newer close is the row's own day, the bar that had not completed when the
    session ran. A crypto mark two bars behind is still stale.
    """
    return (
        price_date is not None
        and snapshot_date == session_date
        and marks_at_previous_bar(ticker)
        and newer_served(raw_head, price_date, _day_before(snapshot_date)) is None
    )


@dataclass(frozen=True)
class Mark:
    book: str
    snapshot_date: str
    session_date: str
    commit: str
    ticker: str
    price_date: str | None
    served_date: str | None
    by_design: bool = False

    @property
    def late(self) -> bool:
        """Hindsight found a newer close for the row's date than the one used."""
        return self.price_date is not None and self.served_date is not None

    @property
    def stale(self) -> bool:
        return self.late and not self.by_design


def session_commits(git: Git, since: str) -> list[tuple[str, str]]:
    """``(sha, session_date)`` oldest first, for weekday sessions on or after ``since``."""
    out = git.run(
        "log", "--reverse", "--format=%H%x09%s", "HEAD", "--", SNAPSHOTS_GLOB
    )
    commits: list[tuple[str, str]] = []
    for line in out.splitlines():
        sha, _, subject = line.partition("\t")
        m = SESSION_SUBJECT.match(subject)
        if m and m.group(1) >= since:
            commits.append((sha, m.group(1)))
    return commits


def written_rows(git: Git, sha: str) -> dict[str, list[dict]]:
    """Per book, the snapshot rows ``sha`` added or changed against its first parent."""
    changed = git.run(
        "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", f"{sha}^1", sha,
        "--", SNAPSHOTS_GLOB,
    ) if _has_parent(git, sha) else git.run(
        "ls-tree", "-r", "--name-only", sha, "--", "data/portfolios"
    )
    out: dict[str, list[dict]] = {}
    for path in changed.splitlines():
        if not path.endswith("/snapshots.json"):
            continue
        book = path.split("/")[2]
        after = _json(git.blob(sha, path)) or []
        before = _json(git.blob(f"{sha}^1", path)) or []
        old = {json.dumps(r, sort_keys=True) for r in before if isinstance(r, dict)}
        rows = [
            r for r in after
            if isinstance(r, dict) and json.dumps(r, sort_keys=True) not in old
        ]
        if rows:
            out[book] = rows
    return out


def _has_parent(git: Git, sha: str) -> bool:
    try:
        git.run("rev-parse", "--verify", "--quiet", f"{sha}^1")
    except subprocess.CalledProcessError:
        return False
    return True


def audit(repo: Path, since: str) -> tuple[list[Mark], int]:
    """Every mark of every session-written row since ``since``, and the session count."""
    git = Git(repo)
    try:
        commits = session_commits(git, since)
        latest: dict[tuple[str, str], list[Mark]] = {}
        head_store: dict[str, bytes | None] = {}
        for sha, session in commits:
            for book, rows in written_rows(git, sha).items():
                portfolio = _json(git.blob(sha, f"data/portfolios/{book}/portfolio.json")) or {}
                positions = [
                    p.get("ticker") for p in portfolio.get("positions", [])
                    if isinstance(p, dict) and p.get("ticker")
                ]
                for row in rows:
                    day = row.get("date")
                    if not isinstance(day, str):
                        continue
                    marks: list[Mark] = []
                    for ticker in positions:
                        path = f"{STORE}/{ticker}.jsonl"
                        priced = price_date_on_or_before(git.blob(sha, path), day)
                        served = None
                        by_design = False
                        if priced is not None:
                            if ticker not in head_store:
                                head_store[ticker] = git.blob("HEAD", path)
                            served = newer_served(head_store[ticker], priced, day)
                            by_design = served is not None and is_by_design(
                                ticker, day, session, priced, head_store[ticker]
                            )
                        marks.append(
                            Mark(book, day, session, sha[:9], ticker, priced, served, by_design)
                        )
                    latest[(book, day)] = marks
        return [m for ms in latest.values() for m in ms], len(commits)
    finally:
        git.close()


def report(marks: list[Mark], sessions: int, since: str) -> str:
    stale = [m for m in marks if m.stale]
    unpriced = [m for m in marks if m.price_date is None]
    lines = [
        f"Weekday sessions audited since {since}: {sessions}",
        f"Snapshot rows audited: {len({(m.book, m.snapshot_date) for m in marks})}",
        f"Held-position marks: {len(marks)}",
        f"Stale marks (an older close used for a date the vendor did serve): "
        f"{len(stale)} ({100 * len(stale) / len(marks):.1f}%)" if marks else "Stale marks: 0",
    ]
    by_design = [m for m in marks if m.late and m.by_design]
    if by_design:
        lines.append(
            f"Crypto/FX/futures marks at the previous completed bar by design "
            f"(not counted stale): {len(by_design)}"
        )
    if unpriced:
        lines.append(f"Marks with no stored close at all: {len(unpriced)}")
    total_by_book = Counter(m.book for m in marks)
    stale_by_book = Counter(m.book for m in stale)
    lines += ["", "By book (stale / marks):"]
    for book in sorted(total_by_book):
        lines.append(f"  {book:24s} {stale_by_book[book]:5d} / {total_by_book[book]}")
    stale_by_ticker = Counter(m.ticker for m in stale)
    lines += ["", "By ticker (stale marks):"]
    for ticker, n in sorted(stale_by_ticker.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {ticker:12s} {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--since", default="2026-08-20")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", help="print every stale mark as JSON")
    args = parser.parse_args(argv)
    try:
        marks, sessions = audit(args.repo, args.since)
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        print(f"audit_stale_marks: could not run: {exc}", file=sys.stderr)
        return 2
    if sessions == 0 or not marks:
        print(f"audit_stale_marks: no session-written marks since {args.since}", file=sys.stderr)
        return 2
    print(report(marks, sessions, args.since))
    if args.json:
        print(json.dumps([m.__dict__ for m in marks if m.stale], indent=1))
    return 1 if any(m.stale for m in marks) else 0


if __name__ == "__main__":
    sys.exit(main())
