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
        """Good Friday 2026-04-03 closed, Easter Monday 04-06 closed: the
        Tuesday session reads Thursday 04-02's close (the session runs before
        its own day's close exists, so that bar is not in the store yet, at
        any collection hour) — 4 days of
        legitimate lag. This is the false-positive the threshold exists to
        clear; it must pass."""
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
