"""`scripts/audit_stale_marks.py` finds a planted stale mark, and only that.

The audit is the source of the historical disclosure in METHODOLOGY
(`#stale-marks-2026-10-03`), so it has to be able to say both "yes" and "no".
Each test builds a throwaway git history shaped like the real one: a weekday
session commit writing a snapshot row, then a later data commit in which the
vendor's close for that date lands.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.audit_stale_marks import audit, main, newer_served, price_date_on_or_before


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


def _write(repo: Path, rel: str, content: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _store(rows: dict[str, float]) -> str:
    return "".join(
        json.dumps({"date": d, "close": c}) + "\n" for d, c in sorted(rows.items())
    )


def _history(tmp_path: Path, *, late_close_lands: bool, fresh_at_session: bool) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    at_session = {"2026-09-28": 100.0, "2026-09-29": 101.0}
    if fresh_at_session:
        at_session["2026-09-30"] = 102.0
    _write(repo, "data/market/ohlcv/GOLD.DE.jsonl", _store(at_session))
    _write(repo, "data/market/ohlcv/FRESH.DE.jsonl", _store({"2026-09-30": 50.0}))
    _write(
        repo,
        "data/portfolios/book/portfolio.json",
        json.dumps({"cash": 0.0, "currency": "EUR", "positions": [
            {"ticker": "GOLD.DE", "shares": 1.0}, {"ticker": "FRESH.DE", "shares": 1.0},
        ]}),
    )
    _write(repo, "data/portfolios/book/snapshots.json", json.dumps([]))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")

    _write(
        repo,
        "data/portfolios/book/snapshots.json",
        json.dumps([{"date": "2026-09-30", "session_date": "2026-10-01", "portfolio_value": 1.0}]),
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "chore: weekday session 2026-10-01")

    if late_close_lands:
        _write(
            repo,
            "data/market/ohlcv/GOLD.DE.jsonl",
            _store({"2026-09-28": 100.0, "2026-09-29": 101.0, "2026-09-30": 102.0}),
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "[data] 2026-10-02 OHLCV update")
    return repo


def test_a_planted_stale_mark_is_found(tmp_path: Path) -> None:
    repo = _history(tmp_path, late_close_lands=True, fresh_at_session=False)
    marks, sessions = audit(repo, "2026-08-20")
    assert sessions == 1
    stale = [m for m in marks if m.stale]
    assert [(m.book, m.snapshot_date, m.ticker, m.price_date, m.served_date) for m in stale] == [
        ("book", "2026-09-30", "GOLD.DE", "2026-09-29", "2026-09-30")
    ]
    assert len(marks) == 2  # FRESH.DE was audited and is not stale
    assert main(["--repo", str(repo)]) == 1


def test_the_control_finds_nothing_when_the_close_was_there(tmp_path: Path) -> None:
    repo = _history(tmp_path, late_close_lands=False, fresh_at_session=True)
    marks, _ = audit(repo, "2026-08-20")
    assert len(marks) == 2
    assert not any(m.stale for m in marks)
    assert main(["--repo", str(repo)]) == 0


def test_a_close_that_never_landed_is_not_counted(tmp_path: Path) -> None:
    """Hindsight, not suspicion: an older mark the vendor never improved on is
    not a stale mark (a holiday looks exactly like this)."""
    repo = _history(tmp_path, late_close_lands=False, fresh_at_session=False)
    marks, _ = audit(repo, "2026-08-20")
    assert not any(m.stale for m in marks)


def test_nothing_to_audit_is_unknown_not_healthy(tmp_path: Path) -> None:
    repo = _history(tmp_path, late_close_lands=True, fresh_at_session=False)
    assert main(["--repo", str(repo), "--since", "2027-01-01"]) == 2


def test_price_date_matches_the_store_reader() -> None:
    raw = _store({"2026-09-29": 1.0, "2026-09-28": 2.0, "2026-10-01": 3.0}).encode()
    assert price_date_on_or_before(raw, "2026-09-30") == "2026-09-29"
    assert price_date_on_or_before(raw, "2026-09-27") is None
    assert newer_served(raw, "2026-09-28", "2026-09-30") == "2026-09-29"
    assert newer_served(raw, "2026-09-29", "2026-09-30") is None


def _same_day_history(tmp_path: Path, *, crypto_store: dict[str, float]) -> Path:
    """A post-2026-09-28 session: the row is dated on its own session's day."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, "data/market/ohlcv/BTC-EUR.jsonl", _store(crypto_store))
    _write(repo, "data/market/ohlcv/AUDUSD=X.jsonl", _store({"2026-09-28": 0.66}))
    _write(repo, "data/market/ohlcv/GOLD.DE.jsonl", _store({"2026-09-28": 100.0}))
    _write(
        repo,
        "data/portfolios/book/portfolio.json",
        json.dumps({"cash": 0.0, "currency": "EUR", "positions": [
            {"ticker": "BTC-EUR", "shares": 1.0},
            {"ticker": "AUDUSD=X", "shares": 1.0},
            {"ticker": "GOLD.DE", "shares": 1.0},
        ]}),
    )
    _write(repo, "data/portfolios/book/snapshots.json", json.dumps([]))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    _write(
        repo,
        "data/portfolios/book/snapshots.json",
        json.dumps([{"date": "2026-09-29", "session_date": "2026-09-29", "portfolio_value": 1.0}]),
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "chore: weekday session 2026-09-29")
    # Every bar lands afterwards: the 09-29 crypto/FX bars completed at midnight.
    later = dict(crypto_store, **{"2026-09-29": 2.0})
    _write(repo, "data/market/ohlcv/BTC-EUR.jsonl", _store(later))
    _write(repo, "data/market/ohlcv/AUDUSD=X.jsonl", _store({"2026-09-28": 0.66, "2026-09-29": 0.67}))
    _write(repo, "data/market/ohlcv/GOLD.DE.jsonl", _store({"2026-09-28": 100.0, "2026-09-29": 101.0}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "[data] 2026-09-30 OHLCV update")
    return repo


def test_previous_completed_bar_marks_are_by_design_not_stale(tmp_path: Path) -> None:
    """Regression (review of feat/stage1-asof-reads, finding 1): the 2026-09-29
    session marked 11 crypto/FX positions at the 09-28 bar, exactly as the
    2026-09-28 cadence move intends, and the hindsight test counted all 11 in
    the published stale-mark disclosure with a vendor-hole explanation.
    Without the fix BTC-EUR and AUDUSD=X are counted stale here."""
    repo = _same_day_history(tmp_path, crypto_store={"2026-09-28": 1.0})
    marks, _ = audit(repo, "2026-08-20")
    by_ticker = {m.ticker: m for m in marks}
    assert by_ticker["BTC-EUR"].by_design and not by_ticker["BTC-EUR"].stale
    assert by_ticker["AUDUSD=X"].by_design and not by_ticker["AUDUSD=X"].stale
    # The control: an equity in the same row, one day late, is still stale.
    assert by_ticker["GOLD.DE"].stale and not by_ticker["GOLD.DE"].by_design


def test_a_crypto_mark_two_bars_behind_is_still_stale(tmp_path: Path) -> None:
    """The exclusion covers one completed bar, not any crypto lateness: a
    09-27 mark in a 09-29 row skipped the 09-28 bar the vendor did serve."""
    repo = _same_day_history(tmp_path, crypto_store={"2026-09-27": 1.0})
    _write(repo, "data/market/ohlcv/BTC-EUR.jsonl",
           _store({"2026-09-27": 1.0, "2026-09-28": 1.5, "2026-09-29": 2.0}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "[data] backfill 09-28")
    btc = next(m for m in audit(repo, "2026-08-20")[0] if m.ticker == "BTC-EUR")
    assert btc.price_date == "2026-09-27"
    assert btc.stale and not btc.by_design
