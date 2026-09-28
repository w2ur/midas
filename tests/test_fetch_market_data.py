"""Tests for scripts.fetch_market_data — store-only by default.

Critical contract: the trading session sandbox is HTTP-blocked. The default
code path must succeed using only files committed to data/market/ohlcv/,
with no outbound network call. yfinance is opt-in via --allow-network for
local dev.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from engine.config import get_config

# Every store fixture below is dated 2026-04-24/25/26, so the freshness gate
# (W3.1) needs a reference "today" in the same week or it correctly refuses a
# 100-day-old store. Passed explicitly rather than frozen globally: the gate's
# own tests need to move this date, and a global freeze would hide that.
_REFERENCE_TODAY = date(2026, 4, 27)


@pytest.fixture
def tmp_store(midas_data_root) -> Path:
    store = get_config().ohlcv_dir
    store.mkdir(parents=True, exist_ok=True)
    return store


def _write_ohlcv(store: Path, ticker: str, rows: list[dict]) -> None:
    path = store / f"{ticker}.jsonl"
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


class TestFetchAndSaveStoreOnly:
    def test_resolves_all_benchmarks_from_store_no_network(
        self, tmp_store: Path, tmp_path: Path
    ) -> None:
        from scripts.fetch_market_data import fetch_and_save

        # Seed store with primary tickers for every benchmark.
        _write_ohlcv(tmp_store, "^GSPC", [{"date": "2026-04-25", "close": 7139.4}])
        _write_ohlcv(tmp_store, "URTH", [{"date": "2026-04-25", "close": 195.27}])
        _write_ohlcv(tmp_store, "GC=F", [{"date": "2026-04-25", "close": 4725.4}])
        _write_ohlcv(tmp_store, "BTC-USD", [{"date": "2026-04-25", "close": 77354.34}])

        out = tmp_path / "today.json"
        payload = fetch_and_save(
            output_path=out, allow_network=False, today=_REFERENCE_TODAY
        )

        assert payload["benchmarks"]["sp500"] == 7139.4
        assert payload["benchmarks"]["msci_world"] == 195.27
        assert payload["benchmarks"]["gold"] == 4725.4
        assert payload["benchmarks"]["btc"] == 77354.34
        assert payload["notes"]["sp500_source"].startswith("^GSPC")
        assert "OHLCV store" in payload["notes"]["sp500_source"]

        # Verify written file matches.
        loaded = json.loads(out.read_text())
        assert loaded == payload

    def test_falls_back_to_proxy_when_primary_missing(
        self, tmp_store: Path, tmp_path: Path
    ) -> None:
        """When ^GSPC is missing, SPY × 10 proxy must kick in."""
        from scripts.fetch_market_data import fetch_and_save

        # No ^GSPC, but SPY present.
        _write_ohlcv(tmp_store, "SPY", [{"date": "2026-04-25", "close": 700.0}])
        _write_ohlcv(tmp_store, "URTH", [{"date": "2026-04-25", "close": 195.0}])
        _write_ohlcv(tmp_store, "GC=F", [{"date": "2026-04-25", "close": 4700.0}])
        _write_ohlcv(tmp_store, "BTC-USD", [{"date": "2026-04-25", "close": 77000.0}])

        payload = fetch_and_save(
            output_path=tmp_path / "out.json",
            allow_network=False,
            today=_REFERENCE_TODAY,
        )

        assert payload["benchmarks"]["sp500"] == 7000.0
        assert "SPY*10 proxy" in payload["notes"]["sp500_source"]

    def test_uses_most_recent_date_across_all_benchmarks(
        self, tmp_store: Path, tmp_path: Path
    ) -> None:
        """Snapshot date is the latest across all sources — useful when crypto
        has Saturday data and equities don't."""
        from scripts.fetch_market_data import fetch_and_save

        _write_ohlcv(tmp_store, "^GSPC", [{"date": "2026-04-24", "close": 7139.0}])
        _write_ohlcv(tmp_store, "URTH", [{"date": "2026-04-24", "close": 195.0}])
        _write_ohlcv(tmp_store, "GC=F", [{"date": "2026-04-24", "close": 4700.0}])
        # Crypto fresh from weekend cron.
        _write_ohlcv(tmp_store, "BTC-USD", [{"date": "2026-04-26", "close": 77000.0}])

        payload = fetch_and_save(
            output_path=tmp_path / "out.json",
            allow_network=False,
            today=_REFERENCE_TODAY,
        )
        assert payload["date"] == "2026-04-26"

    def test_raises_when_no_source_for_benchmark(
        self, tmp_store: Path, tmp_path: Path
    ) -> None:
        """If the store has nothing for a benchmark, fail loudly — silent
        zeros would lie to the agents."""
        from scripts.fetch_market_data import fetch_and_save

        # Only seed BTC; everything else missing.
        _write_ohlcv(tmp_store, "BTC-USD", [{"date": "2026-04-25", "close": 77000.0}])

        with pytest.raises(RuntimeError, match="No OHLCV source"):
            fetch_and_save(
                output_path=tmp_path / "out.json",
                allow_network=False,
                today=_REFERENCE_TODAY,
            )

    def test_default_does_not_call_yfinance(
        self, tmp_store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard: default fetch_and_save() must never call into
        the network path. The trading sandbox depends on this."""
        import scripts.fetch_market_data as fmd

        called = {"network": False}

        def _explode(*_args, **_kwargs):
            called["network"] = True
            raise AssertionError("network path was called")

        monkeypatch.setattr(fmd, "_fetch_with_network", _explode)

        _write_ohlcv(tmp_store, "^GSPC", [{"date": "2026-04-25", "close": 7000.0}])
        _write_ohlcv(tmp_store, "URTH", [{"date": "2026-04-25", "close": 195.0}])
        _write_ohlcv(tmp_store, "GC=F", [{"date": "2026-04-25", "close": 4700.0}])
        _write_ohlcv(tmp_store, "BTC-USD", [{"date": "2026-04-25", "close": 77000.0}])

        fmd.fetch_and_save(output_path=tmp_path / "out.json", today=_REFERENCE_TODAY)
        assert called["network"] is False


# ---------------------------------------------------------------------------
# Store-freshness gate (2026-08-07 review, W3.1)
# ---------------------------------------------------------------------------


def _seed_all(store: Path, *, equity: str, crypto: str | None = None) -> None:
    """Seed all four benchmarks; equities on *equity*, BTC on *crypto*."""
    crypto = crypto or equity
    _write_ohlcv(store, "^GSPC", [{"date": equity, "close": 7139.0}])
    _write_ohlcv(store, "URTH", [{"date": equity, "close": 195.0}])
    _write_ohlcv(store, "GC=F", [{"date": equity, "close": 4700.0}])
    _write_ohlcv(store, "BTC-USD", [{"date": crypto, "close": 77000.0}])


class TestEquityFreshnessGate:
    """The gate must refuse a store that stopped advancing, and must NOT
    refuse the ordinary long-weekend lag that a healthy store shows every
    time a session runs before that evening's OHLCV cron."""

    def test_stale_equity_store_aborts_the_session(
        self, tmp_store: Path, tmp_path: Path
    ) -> None:
        from scripts.fetch_market_data import StaleMarketDataError, fetch_and_save

        # Equity feed died two weeks ago; crypto kept advancing, which is
        # exactly what a max-over-all-benchmarks date would hide.
        _seed_all(tmp_store, equity="2026-04-13", crypto="2026-04-27")

        with pytest.raises(StaleMarketDataError, match="14 calendar days stale"):
            fetch_and_save(
                output_path=tmp_path / "out.json",
                allow_network=False,
                today=_REFERENCE_TODAY,
            )
        # Nothing published: an aborted session must not leave a today.json
        # that a later step could read as current.
        assert not (tmp_path / "out.json").exists()

    def test_gate_passes_at_the_limit_and_fails_one_day_past_it(
        self, tmp_store: Path, tmp_path: Path
    ) -> None:
        """The falsifying pair. A threshold only tested on one side is a
        threshold that could be anywhere."""
        from scripts.fetch_market_data import (
            MAX_EQUITY_STALENESS_DAYS,
            StaleMarketDataError,
            fetch_and_save,
        )
        from datetime import timedelta

        today = date(2026, 4, 27)
        at_limit = today - timedelta(days=MAX_EQUITY_STALENESS_DAYS)
        past_limit = today - timedelta(days=MAX_EQUITY_STALENESS_DAYS + 1)

        _seed_all(tmp_store, equity=at_limit.isoformat())
        payload = fetch_and_save(
            output_path=tmp_path / "ok.json", allow_network=False, today=today
        )
        assert payload["equity_date"] == at_limit.isoformat()

        _seed_all(tmp_store, equity=past_limit.isoformat())
        with pytest.raises(StaleMarketDataError):
            fetch_and_save(
                output_path=tmp_path / "bad.json", allow_network=False, today=today
            )

    def test_easter_style_holiday_weekend_is_not_stale(
        self, tmp_store: Path, tmp_path: Path
    ) -> None:
        """Good Friday 2026-04-03 closed, then the weekend: the **Monday**
        04-06 session reads Thursday 04-02's close — the session runs before
        its own day's close exists, so that bar is not in the store yet, at
        any collection hour. 4 days of legitimate lag, and the false positive
        the threshold exists to clear; it must pass.

        The fixture day is Monday, and that is the arithmetic: 04-02 to 04-06
        is exactly the 4 calendar days allowed, where a Tuesday 04-07 session
        would be 5 and should fail. This docstring said "Tuesday" until
        2026-08-12 while passing `date(2026, 4, 6)` — the fixture was the
        correct half of that disagreement.
        """
        from scripts.fetch_market_data import fetch_and_save

        _seed_all(tmp_store, equity="2026-04-02", crypto="2026-04-06")

        payload = fetch_and_save(
            output_path=tmp_path / "out.json",
            allow_network=False,
            today=date(2026, 4, 6),
        )
        assert payload["equity_date"] == "2026-04-02"

    def test_mixed_dates_are_recorded_not_silent(
        self, tmp_store: Path, tmp_path: Path
    ) -> None:
        """A weekend/holiday row is dated on the freshest benchmark (crypto)
        while equity positions are marked at the last equity close. That is a
        correct mark — `latest_price` reads the last close on-or-before the
        date — but it used to be invisible in the artifact."""
        from scripts.fetch_market_data import fetch_and_save

        _seed_all(tmp_store, equity="2026-04-24", crypto="2026-04-26")

        payload = fetch_and_save(
            output_path=tmp_path / "out.json",
            allow_network=False,
            today=_REFERENCE_TODAY,
        )
        assert payload["date"] == "2026-04-26"
        assert payload["equity_date"] == "2026-04-24"
        assert "2026-04-24" in payload["notes"]["mixed_dates"]

    def test_no_mixed_dates_note_when_all_benchmarks_agree(
        self, tmp_store: Path, tmp_path: Path
    ) -> None:
        """The control for the test above: the note must be absent on an
        ordinary weekday, or its presence says nothing."""
        from scripts.fetch_market_data import fetch_and_save

        _seed_all(tmp_store, equity="2026-04-24")

        payload = fetch_and_save(
            output_path=tmp_path / "out.json",
            allow_network=False,
            today=_REFERENCE_TODAY,
        )
        assert payload["date"] == payload["equity_date"] == "2026-04-24"
        assert "mixed_dates" not in payload["notes"]


# ---------------------------------------------------------------------------
# Same-evening close runs (2026-09-28): the other mixed-dates direction, and
# per-exchange freshness
# ---------------------------------------------------------------------------


class TestEquitiesFresherThanCrypto:
    """With the cash closes collected the same evening, the equity benchmarks
    carry today's close while BTC and gold are at the previous completed UTC
    bar. The row is dated on the equity close (the max), and the note says
    which positions are a bar behind — the mirror of the weekend case."""

    def test_the_inverse_mixed_dates_case_is_recorded(
        self, tmp_store: Path, tmp_path: Path
    ) -> None:
        from scripts.fetch_market_data import fetch_and_save

        _seed_all(tmp_store, equity="2026-04-27", crypto="2026-04-26")
        _write_ohlcv(tmp_store, "GC=F", [{"date": "2026-04-26", "close": 4700.0}])

        payload = fetch_and_save(
            output_path=tmp_path / "out.json",
            allow_network=False,
            today=_REFERENCE_TODAY,
        )
        assert payload["date"] == payload["equity_date"] == "2026-04-27"
        note = payload["notes"]["mixed_dates"]
        assert "equity close" in note
        assert "btc at 2026-04-26" in note and "gold at 2026-04-26" in note

    def test_the_weekend_direction_is_unchanged(
        self, tmp_store: Path, tmp_path: Path
    ) -> None:
        """The control: the original wording survives for the original case."""
        from scripts.fetch_market_data import fetch_and_save

        _seed_all(tmp_store, equity="2026-04-24", crypto="2026-04-26")

        payload = fetch_and_save(
            output_path=tmp_path / "out.json",
            allow_network=False,
            today=_REFERENCE_TODAY,
        )
        assert payload["notes"]["mixed_dates"].startswith("snapshot dated 2026-04-26 (crypto/gold)")


class TestExchangeDates:
    """`exchange_dates` answers which close each exchange's positions were
    marked at, which one US-listed `equity_date` cannot."""

    def _seed_exchange(self, store: Path, suffix: str, dates: list[str]) -> None:
        for i, d in enumerate(dates):
            _write_ohlcv(store, f"X{i}{suffix}", [{"date": d, "close": 10.0}])

    def test_reports_the_close_at_least_half_the_bucket_holds(self, tmp_store: Path) -> None:
        from scripts.fetch_market_data import exchange_dates

        # 4 of 6 at 04-27, 2 day-late funds at 04-24: the exchange is at 04-27.
        self._seed_exchange(tmp_store, ".DE", ["2026-04-27"] * 4 + ["2026-04-24"] * 2)
        # One first-ingest file ahead of the other five must NOT speak for `.PA`.
        self._seed_exchange(tmp_store, ".PA", ["2026-04-27"] + ["2026-04-24"] * 5)
        assert exchange_dates(tmp_store) == {".DE": "2026-04-27", ".PA": "2026-04-24"}

    def test_small_buckets_and_non_cash_instruments_are_left_out(self, tmp_store: Path) -> None:
        from scripts.fetch_market_data import exchange_dates

        self._seed_exchange(tmp_store, ".F", ["2026-04-27"])
        for sym in ("GC=F", "EURUSD=X", "^VIX", "BTC-USD", "ADA-EUR"):
            _write_ohlcv(tmp_store, sym, [{"date": "2026-04-27", "close": 1.0}])
        assert exchange_dates(tmp_store) == {}

    def test_us_listings_form_the_us_bucket_dashes_included(self, tmp_store: Path) -> None:
        from scripts.fetch_market_data import exchange_dates

        for sym in ("AAPL", "MSFT", "BRK-B", "BF-B", "SPY"):
            _write_ohlcv(tmp_store, sym, [{"date": "2026-04-27", "close": 1.0}])
        assert exchange_dates(tmp_store) == {"US": "2026-04-27"}

    def test_an_exchange_behind_the_equity_date_is_named_in_the_payload(
        self, tmp_store: Path, tmp_path: Path
    ) -> None:
        """The evening pass landed the US close but not London's: the LSE books
        are marked a day behind the row, and the bundle says so."""
        from scripts.fetch_market_data import fetch_and_save

        _seed_all(tmp_store, equity="2026-04-27")
        self._seed_exchange(tmp_store, ".L", ["2026-04-24"] * 5)
        for sym in ("AAPL", "MSFT", "BRK-B", "BF-B", "QQQ"):
            _write_ohlcv(tmp_store, sym, [{"date": "2026-04-27", "close": 1.0}])

        payload = fetch_and_save(
            output_path=tmp_path / "out.json", allow_network=False, today=_REFERENCE_TODAY
        )
        assert payload["notes"]["exchange_dates"] == {".L": "2026-04-24", "US": "2026-04-27"}
        assert ".L at 2026-04-24" in payload["notes"]["exchange_behind"]
        assert "exchange_ahead" not in payload["notes"]

    def test_an_exchange_ahead_of_the_row_date_is_named_as_a_mislabel(
        self, tmp_store: Path, tmp_path: Path
    ) -> None:
        """A session that ran after the European pass but before the US one:
        the row is dated on the US benchmarks (yesterday) while the European
        positions inside it carry today's close. Said out loud, because the
        row is immutable once published."""
        from scripts.fetch_market_data import fetch_and_save

        _seed_all(tmp_store, equity="2026-04-24")
        self._seed_exchange(tmp_store, ".PA", ["2026-04-27"] * 5)

        payload = fetch_and_save(
            output_path=tmp_path / "out.json", allow_network=False, today=_REFERENCE_TODAY
        )
        assert payload["date"] == "2026-04-24"
        assert ".PA at 2026-04-27" in payload["notes"]["exchange_ahead"]
