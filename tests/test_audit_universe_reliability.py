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

    def test_a_rewrite_of_a_backfilled_row_is_not_a_late_arrival(self, repo: Path):
        # Regression: FRO.jsonl was created by a backfill (new file, skipped),
        # then a resweep rewrote its rows as -/+ pairs; the `+` was read as the
        # first arrival and on-time backfilled rows counted as late.
        self._commit(repo, "2026-09-10T06:00:00Z", FRO=["2026-08-03", "2026-08-04"])
        path = repo / "ohlcv" / "FRO.jsonl"
        rows = [json.loads(x) for x in path.read_text().splitlines()]
        path.write_text("".join(json.dumps({**r, "close": 2.0}) + "\n" for r in rows))
        _git(repo, "add", "-A")
        when = "2026-09-21T06:00:00Z"
        env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when,
               "PATH": __import__("os").environ["PATH"], "HOME": str(repo)}
        _git(repo, "commit", "-q", "-m", "resweep", env=env)
        assert audit._late_rows("2026-08-01", "ohlcv", repo) == {}
        # control: a genuinely new late row in the same file is still counted
        self._commit(repo, "2026-09-22T06:00:00Z", FRO=["2026-08-05"])
        assert audit._late_rows("2026-08-01", "ohlcv", repo) == {"FRO": 1}


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
        TestLateRows._commit(repo, "2026-08-10T06:00:00Z", **{"HBAR-USD": ["2026-08-08"]})
        assert audit._late_rows("2026-08-01", "ohlcv", repo, crypto=self.CRYPTO) == {"HBAR-USD": 1}
        assert audit._late_rows("2026-08-01", "ohlcv", repo) == {}  # control


class TestMissingFx:
    ROWS = [("2026-09-01", 10.0, 100.0)]

    def test_refuses_local_units_when_no_rate_exists(self, monkeypatch):
        # Regression: a None rate fell back to the unconverted local value, so
        # SEK/NOK/DKK/PLN names ranked 4-11x too high with no symptom.
        monkeypatch.setattr(audit, "ticker_currency", lambda s: "XYZ")
        monkeypatch.setattr(audit.fx, "to_eur", lambda amount, ccy: None)
        with pytest.raises(audit.NoFxRate):
            audit._median_value_eur("ERIC-B.ST", self.ROWS, 60)

    def test_control_a_rate_converts(self, monkeypatch):
        monkeypatch.setattr(audit, "ticker_currency", lambda s: "SEK")
        monkeypatch.setattr(audit.fx, "to_eur", lambda amount, ccy: amount / 10)
        assert audit._median_value_eur("ERIC-B.ST", self.ROWS, 60) == 100.0

    def test_a_currency_without_a_store_rate_ranks_on_the_approximate_rate(self, monkeypatch):
        # Regression: SEK/NOK/DKK/PLN made the default run exit 2, and the only
        # escape put 111 symbols in every cut's floor.
        monkeypatch.setattr(audit, "ticker_currency", lambda s: "SEK")
        monkeypatch.setattr(audit.fx, "to_eur", lambda amount, ccy: None)
        used: set[str] = set()
        v = audit._median_value_eur("ERIC-B.ST", self.ROWS, 60, used)
        assert v == pytest.approx(1000.0 * audit.RANKING_ONLY_EUR_RATES["SEK"])
        assert used == {"SEK"}

    def test_a_currency_in_neither_source_still_refuses(self, monkeypatch):
        monkeypatch.setattr(audit, "ticker_currency", lambda s: "XYZ")
        monkeypatch.setattr(audit.fx, "to_eur", lambda amount, ccy: None)
        with pytest.raises(audit.NoFxRate):
            audit._median_value_eur("X.ZZ", self.ROWS, 60)

    def test_an_unresolved_currency_is_unrankable_not_eur(self, monkeypatch):
        # Regression: None was treated as EUR, so the raw local value ranked.
        monkeypatch.setattr(audit, "ticker_currency", lambda s: None)
        with pytest.raises(audit.NoFxRate):
            audit._median_value_eur("X.ZZ", self.ROWS, 60)

    def test_no_volume_is_still_none_not_an_error(self, monkeypatch):
        assert audit._median_value_eur("X", [("2026-09-01", 1.0, None)], 60) is None


class TestValueBasis:
    ROWS = [("2026-09-01", 84000.0, 5_000_000.0)]

    def test_crypto_volume_is_already_quote_currency(self, monkeypatch):
        # Regression: close x volume ranked BTC (close 84k, volume in USD) above SPY
        # and SHIB-USD ($113M a day) dead last.
        monkeypatch.setattr(audit.fx, "to_eur", lambda amount, ccy: amount)
        assert audit._median_value_eur("BTC-USD", self.ROWS, 60) == 5_000_000.0
        assert audit._median_value_eur("HBAR-USD", self.ROWS, 60, crypto=frozenset({"HBAR-USD"})) == 5_000_000.0
        # control: an equity is still volume x close
        assert audit._median_value_eur("SPY", self.ROWS, 60) == 84000.0 * 5_000_000.0

    def test_a_cheap_liquid_coin_outranks_an_expensive_thin_one(self, monkeypatch):
        monkeypatch.setattr(audit.fx, "to_eur", lambda amount, ccy: amount)
        shib = audit._median_value_eur("SHIB-USD", [("2026-09-01", 1e-5, 113e6)], 60)
        thin = audit._median_value_eur("BTC-USD", [("2026-09-01", 84000.0, 1e3)], 60)
        assert shib > thin

    def test_futures_are_unrankable_not_ranked_by_price(self):
        # Regression: contract counts x per-oz close put GC=F at rank 1205.
        assert audit._median_value_eur("GC=F", [("2026-09-01", 4000.0, 200000.0)], 60) is None


class TestEventCounting:
    def test_a_ledgered_missing_day_is_one_event_not_two(self):
        # Regression: BYND 2026-08-13 sat in both member_gaps and the ledger.
        assert audit._unledgered_gaps(frozenset({"2026-08-13"}), {"2026-08-13": {}}) == 0
        # control: an unledgered gap still counts
        assert audit._unledgered_gaps(frozenset({"2026-08-13", "2026-08-14"}), {"2026-08-13": {}}) == 1

    def test_repeat_quarantine_attempts_count_once_per_date(self, tmp_path):
        # Regression: MNST had 11 quarantine lines over 5 distinct dates.
        qdir = tmp_path / "q"
        qdir.mkdir()
        lines = [{"symbol": "MNST", "date": "2026-08-10"}] * 3 + [
            {"symbol": "MNST", "date": "2026-08-11"},
            {"symbol": "MNST", "date": "2026-07-01"},  # before the window
        ]
        (qdir / "MNST.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
        assert audit._quarantine_counts(qdir, "2026-08-01") == {"MNST": 2}


class TestQuarantineOverlap:
    def test_a_quarantined_day_already_ledgered_or_gapped_is_one_event(self):
        # Regression: BYND 2026-08-13 was accepted in the ledger AND quarantined,
        # so the one missing day was reported as two events.
        q = {"2026-08-13", "2026-08-14", "2026-08-17"}
        assert audit._unledgered_quarantine(q, frozenset({"2026-08-17"}), {"2026-08-13": {}}) == 1
        # control: nothing overlapping still counts in full
        assert audit._unledgered_quarantine(q, frozenset(), {}) == 3

    def test_quarantine_dates_keep_the_dates_not_just_the_count(self, tmp_path):
        (tmp_path / "BYND.jsonl").write_text(json.dumps({"symbol": "BYND", "date": "2026-08-13"}) + "\n")
        assert audit._quarantine_dates(tmp_path, "2026-08-01") == {"BYND": {"2026-08-13"}}


class TestFloorExtra:
    def test_unrankable_symbols_are_not_ranked_below_n(self):
        # Regression: rank_of.get(sym, 10**9) counted the 12 FX pairs as ranked below n.
        rows = _universe()
        ranked = sorted((r for r in rows if r.value_eur is not None), key=lambda r: -r.value_eur)
        rank_of = {r.symbol: i + 1 for i, r in enumerate(ranked)}
        forced = {r.symbol for r in rows if r.held or r.ordered or r.benchmark or r.value_eur is None}
        extra = {r.symbol for r in audit._floor_extra(rows, forced, rank_of, 3)}
        assert "EURUSD=X" not in extra
        # control: ranked floor symbols below n still count
        assert extra == {"THIN_HELD", "THIN_ORDERED", "THIN_BENCH"}


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
        assert "the OHLCV store is empty" in capsys.readouterr().err


class TestHeaderAndValueAge:
    META = {k: 0 for k in (
        "store_files", "universe_symbols", "universe_in_store", "held", "ordered",
        "benchmarks", "pool", "outside_pool_files", "bucket_wide_candidates",
    )}

    def test_the_retired_header_names_the_heaviest_not_the_first_alphabetically(self):
        # Regression: retired[:12] listed the 12 alphabetically first, so a heavy
        # carrier sorting late was omitted.
        retired = [_row(f"A{i:02d}", None, quar=1) for i in range(12)] + [_row("ZZZ", None, quar=40)]
        assert [r.symbol for r in audit._heaviest(retired, 3)][0] == "ZZZ"
        meta = dict(self.META, since="2026-08-01", end="2026-10-02", retired_with_events=13)
        text = audit.format_report([_row("X", 1.0)], retired, meta, [1], 5)
        assert "ZZZ(40)" in text
        # control: a light, late-sorting symbol is the one left out
        assert audit._heaviest(retired, 12)[-1].symbol == "A10"

    def test_value_asof_is_the_newest_ranked_row(self):
        assert audit._value_asof([("2024-04-16", 1.0, 1.0), ("2024-04-15", 1.0, 1.0)], 60) == "2024-04-16"
        assert audit._value_asof([], 60) == ""

    def test_a_value_from_before_the_window_is_flagged(self):
        # Regression: UNI-USD's newest row is 2024-04-16 and was ranked as if current.
        meta = dict(self.META, since="2026-08-01", end="2026-10-02", retired_with_events=0)
        rows = [_row("OLD", 5.0, value_stale=True), _row("NEW", 9.0)]
        text = audit.format_report(rows, [], meta, [1], 5)
        assert "1 symbols are ranked on rows older than the window" in text
        assert any(ln.startswith("OLD") and "5*" in ln for ln in text.splitlines())
        assert not any(ln.startswith("NEW") and "*" in ln for ln in text.splitlines())


class TestOrderedTickers:
    def test_pending_channels_hold_one_json_file_per_order(self, tmp_path):
        # Regression: the pending channels were globbed as *.jsonl and matched
        # nothing, so a conditional order with no outbox line was not "ever
        # ordered" and could be cut.
        (tmp_path / "outbox").mkdir()
        (tmp_path / "outbox" / "2026-09-01.jsonl").write_text('{"ticker": "AAA"}\n')
        for ch, t in (("pending", "BBB"), ("manager-pending", "CCC")):
            (tmp_path / ch).mkdir()
            (tmp_path / ch / "ord_x.json").write_text(json.dumps({"ticker": t}, indent=2))
        (tmp_path / "pending" / "ord_bad.json").write_text("{not json")
        assert audit._ordered_tickers(tmp_path) == {"AAA", "BBB", "CCC"}
