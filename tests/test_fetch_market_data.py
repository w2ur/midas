"""Tests for scripts.fetch_market_data — store-only by default.

Critical contract: the trading session sandbox is HTTP-blocked. The default
code path must succeed using only files committed to data/market/ohlcv/,
with no outbound network call. yfinance is opt-in via --allow-network for
local dev.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from engine.config import get_config


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
        payload = fetch_and_save(output_path=out, allow_network=False)

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

        payload = fetch_and_save(output_path=tmp_path / "out.json", allow_network=False)

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

        payload = fetch_and_save(output_path=tmp_path / "out.json", allow_network=False)
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
            fetch_and_save(output_path=tmp_path / "out.json", allow_network=False)

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

        fmd.fetch_and_save(output_path=tmp_path / "out.json")
        assert called["network"] is False
