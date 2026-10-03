"""scripts/audit_universe_reliability.py: the read-only Stage 6.1 measurement.

Each test builds the situation it claims to detect, and the controls below flip
one input to show the check can answer the other way.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import audit_universe_reliability as audit
from scripts.audit_universe_reliability import SymbolRow, choose_cut


def _row(symbol: str, value: float | None, **kw) -> SymbolRow:
    return SymbolRow(symbol=symbol, bucket="", value_eur=value, **kw)


def _universe() -> list[SymbolRow]:
    return [
        _row("A", 900.0),
        _row("B", 800.0),
        _row("C", 700.0),
        _row("THIN_HELD", 1.0, held=True),
        _row("THIN_ORDERED", 2.0, ordered=True),
        _row("THIN_BENCH", 3.0, benchmark=True),
        _row("THIN_PLAIN", 4.0),
        _row("EURUSD=X", None),
    ]


class TestChooseCut:
    def test_keeps_the_top_n_and_every_always_kept_symbol(self):
        keep = choose_cut(_universe(), 2)
        assert keep == {
            "A", "B", "THIN_HELD", "THIN_ORDERED", "THIN_BENCH", "EURUSD=X",
        }

    def test_a_plain_thin_symbol_is_dropped(self):
        assert "THIN_PLAIN" not in choose_cut(_universe(), 3)

    def test_control_the_floor_is_what_keeps_the_held_symbol(self):
        # Without the held flag the same symbol falls out: the floor is not
        # an accident of its rank.
        rows = [r for r in _universe() if r.symbol != "THIN_HELD"]
        rows.append(_row("THIN_HELD", 1.0))
        assert "THIN_HELD" not in choose_cut(rows, 2)

    def test_unrankable_symbols_are_kept_not_ranked(self):
        assert "EURUSD=X" in choose_cut(_universe(), 1)


def _git(repo: Path, *args: str, env: dict | None = None) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


class TestLateRows:
    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        (repo / "ohlcv").mkdir(parents=True)
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "t")
        return repo

    @staticmethod
    def _commit(repo: Path, when: str, **files: list[str]) -> None:
        for symbol, dates in files.items():
            with (repo / "ohlcv" / f"{symbol}.jsonl").open("a") as fh:
                for d in dates:
                    fh.write(json.dumps({"date": d, "close": 1.0, "volume": 1}) + "\n")
        _git(repo, "add", "-A")
        env = {
            "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when,
            "PATH": __import__("os").environ["PATH"], "HOME": str(repo),
        }
        _git(repo, "commit", "-q", "-m", "rows", env=env)

    def test_counts_a_row_that_arrived_two_business_days_late(self, repo: Path):
        # Created with its first row (a backfill, never counted), then a
        # Monday row arrives on Thursday (3 business days) and a Tuesday row on
        # Wednesday (1: on time).
        self._commit(repo, "2026-08-03T06:00:00Z", LATE=["2026-08-03"], OK=["2026-08-03"])
        self._commit(repo, "2026-08-06T06:00:00Z", LATE=["2026-08-04"])
        self._commit(repo, "2026-08-05T06:00:00Z", OK=["2026-08-04"])
        got = audit._late_rows("2026-08-01", "ohlcv", repo)
        assert got == {"LATE": 1}

    def test_control_an_on_time_series_reports_nothing(self, repo: Path):
        self._commit(repo, "2026-08-04T06:00:00Z", OK=["2026-08-03"])
        self._commit(repo, "2026-08-05T06:00:00Z", OK=["2026-08-04"])
        assert audit._late_rows("2026-08-01", "ohlcv", repo) == {}

    def test_a_diff_hunk_header_is_not_read_as_a_commit_marker(self, repo: Path):
        # `-U0` hunk headers start with "@@"; an earlier marker of that shape
        # crashed on the first real diff.
        self._commit(repo, "2026-08-04T06:00:00Z", OK=["2026-08-03"])
        self._commit(repo, "2026-08-05T06:00:00Z", OK=["2026-08-04"])
        self._commit(repo, "2026-08-06T06:00:00Z", OK=["2026-08-05"])
        assert audit._late_rows("2026-08-01", "ohlcv", repo) == {}


class TestCryptoBucket:
    CRYPTO = frozenset({"HBAR-USD"})

    def test_a_pair_the_fee_allowlist_lacks_is_crypto_with_the_set(self):
        # Regression: the audit called hole_bucket(s) bare, so HBAR-USD landed
        # in the US bucket "" and its weekend row made every US equity stale.
        assert audit.hole_bucket("HBAR-USD") == ""  # control: the bare call is wrong
        assert audit._bucketer(self.CRYPTO)("HBAR-USD") == "crypto"

    def test_a_weekend_crypto_row_does_not_make_us_equities_stale(self):
        dates = {
            "AAPL": frozenset({"2026-09-25"}),
            "HBAR-USD": frozenset({"2026-09-25", "2026-09-27"}),  # 09-27 is a Sunday
        }
        newest = audit._bucket_newest(dates, audit._bucketer(self.CRYPTO))
        assert newest[""] == "2026-09-25"
        assert newest["crypto"] == "2026-09-27"
        # control: the bare bucketing reproduces the defect
        bare = audit._bucket_newest(dates, audit.hole_bucket)
        assert bare[""] == "2026-09-27"

    def test_late_rows_count_crypto_in_calendar_days(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "ohlcv").mkdir(parents=True)
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "t")
        TestLateRows._commit(repo, "2026-08-03T06:00:00Z", **{"HBAR-USD": ["2026-08-01"]})
        # Saturday's row arrives Monday: 2 calendar days late, 0 business days.
        TestLateRows._commit(repo, "2026-08-03T07:00:00Z", **{"HBAR-USD": ["2026-08-01x"]}) if False else None
        TestLateRows._commit(repo, "2026-08-10T06:00:00Z", **{"HBAR-USD": ["2026-08-08"]})
        assert audit._late_rows("2026-08-01", "ohlcv", repo, crypto=self.CRYPTO) == {"HBAR-USD": 1}
        assert audit._late_rows("2026-08-01", "ohlcv", repo) == {}  # control


class TestReport:
    def test_avoided_share_is_the_events_on_dropped_symbols(self):
        rows = [
            _row("KEEP", 100.0, held=True, quar=1),
            _row("DROP", 1.0, quar=3),
        ]
        meta = {k: 0 for k in (
            "store_files", "universe_symbols", "universe_in_store", "held", "ordered",
            "benchmarks", "pool", "outside_pool_files", "retired_with_events",
            "bucket_wide_candidates",
        )}
        meta.update(since="2026-08-01", end="2026-10-02")
        text = audit.format_report(rows, [], meta, [1], 5)
        line = next(ln for ln in text.splitlines() if ln.startswith("top 1 "))
        assert "75%" in line  # 3 of 4 events sit on the dropped symbol

    def test_an_empty_store_is_unknown_not_clean(self, midas_data_root, monkeypatch, capsys):
        monkeypatch.setattr(audit, "_all_symbols", lambda: [])
        monkeypatch.setattr(audit, "_collect_holdings", lambda: set())
        (midas_data_root / "data" / "market" / "ohlcv").mkdir(parents=True)
        assert audit.main([]) == audit.EXIT_UNKNOWN
        assert "could not run" in capsys.readouterr().err
