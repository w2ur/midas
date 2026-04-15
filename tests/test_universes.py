"""Tests for universe resolvers — index, asset class, and alternative data."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from engine.universes.assets import (
    get_crypto_tickers,
    get_forex_tickers,
    get_metals_tickers,
)
from engine.universes.alternative import (
    get_congressional_tickers,
    get_insider_tickers,
    get_high_short_tickers,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CACHE_DIR = _REPO_ROOT / "data" / "cache" / "universes"


# ---------------------------------------------------------------------------
# Task 6 — Index universe resolvers (integration tests)
# ---------------------------------------------------------------------------

class TestSP500Tickers:
    def test_returns_list_of_strings(self, tmp_path, monkeypatch):
        """Cached result returns a non-empty sorted list of strings."""
        # Use a pre-seeded cache to avoid a live network call in CI
        cache_path = _CACHE_DIR / "sp500.json"
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        sample = sorted(["AAPL", "MSFT", "NVDA", "GOOG", "AMZN"])
        cache_path.write_text(json.dumps(sample))

        from engine.universes.index import get_sp500_tickers
        result = get_sp500_tickers()
        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(t, str) for t in result)

    def test_no_dots_in_tickers(self):
        """Tickers must not contain dots (BRK.B → BRK-B)."""
        cache_path = _CACHE_DIR / "sp500.json"
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        sample = ["AAPL", "BRK-B", "BF-B"]
        cache_path.write_text(json.dumps(sample))

        from engine.universes.index import get_sp500_tickers
        result = get_sp500_tickers()
        for ticker in result:
            assert "." not in ticker, f"Ticker {ticker!r} still contains a dot"

    def test_cache_is_sorted(self, monkeypatch, tmp_path):
        """Returned list must be sorted."""
        import engine.universes.index as ix_mod

        fake_dir = tmp_path / "universes"
        fake_dir.mkdir()
        monkeypatch.setattr(ix_mod, "_CACHE_DIR", fake_dir)

        sample = sorted(["AAPL", "BRK-B", "BF-B", "MSFT", "NVDA"])
        (fake_dir / "sp500.json").write_text(json.dumps(sample))

        result = ix_mod.get_sp500_tickers()
        assert result == sorted(result)


class TestDow30Tickers:
    def test_returns_list_of_strings(self):
        """Cached result returns a non-empty list of strings."""
        cache_path = _CACHE_DIR / "dow30.json"
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        sample = sorted(["AAPL", "MSFT", "NVDA", "JPM", "V",
                          "GS", "HD", "MCD", "DIS", "BA",
                          "CAT", "CVX", "IBM", "MMM", "NKE",
                          "PG", "TRV", "UNH", "VZ", "WMT",
                          "AXP", "AMGN", "CRM", "DOW", "HON",
                          "INTC", "JNJ", "KO", "MRK", "WBA"])
        cache_path.write_text(json.dumps(sample))

        from engine.universes.index import get_dow30_tickers
        result = get_dow30_tickers()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_known_members_present(self):
        """Known Dow members must appear in the result."""
        from engine.universes.index import get_dow30_tickers
        result = get_dow30_tickers()
        for ticker in ("AAPL", "MSFT"):
            assert ticker in result, f"Expected {ticker} in Dow 30 but got: {result[:10]}"

    def test_no_dots_in_tickers(self):
        from engine.universes.index import get_dow30_tickers
        result = get_dow30_tickers()
        for ticker in result:
            assert "." not in ticker


class TestNasdaq100Tickers:
    def test_returns_list_of_strings(self):
        """Cached result returns a non-empty list of strings."""
        cache_path = _CACHE_DIR / "nasdaq100.json"
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        sample = sorted(["AAPL", "MSFT", "NVDA", "AMZN", "META",
                          "TSLA", "GOOGL", "GOOG", "AVGO", "COST"])
        cache_path.write_text(json.dumps(sample))

        from engine.universes.index import get_nasdaq100_tickers
        result = get_nasdaq100_tickers()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_known_members_present(self):
        from engine.universes.index import get_nasdaq100_tickers
        result = get_nasdaq100_tickers()
        for ticker in ("AAPL", "MSFT", "NVDA"):
            assert ticker in result, f"Expected {ticker} in Nasdaq-100 but got: {result[:10]}"

    def test_no_dots_in_tickers(self):
        from engine.universes.index import get_nasdaq100_tickers
        result = get_nasdaq100_tickers()
        for ticker in result:
            assert "." not in ticker


class TestIndexCaching:
    def test_cache_hit_skips_fetch(self, monkeypatch, tmp_path):
        """A fresh cache file must be returned without touching the network."""
        import engine.universes.index as ix_mod

        fake_cache_dir = tmp_path / "universes"
        fake_cache_dir.mkdir()
        monkeypatch.setattr(ix_mod, "_CACHE_DIR", fake_cache_dir)

        expected = ["AAPL", "MSFT"]
        cache_file = fake_cache_dir / "sp500.json"
        cache_file.write_text(json.dumps(expected))

        # pd.read_html should never be called when cache is valid
        called = []

        def fake_read_html(*args, **kwargs):
            called.append(True)
            return []

        monkeypatch.setattr("pandas.read_html", fake_read_html)

        result = ix_mod.get_sp500_tickers()
        assert result == expected
        assert not called, "pd.read_html was called despite a valid cache"

    def test_stale_cache_triggers_fetch(self, monkeypatch, tmp_path):
        """An expired cache must trigger a network fetch."""
        import engine.universes.index as ix_mod

        fake_cache_dir = tmp_path / "universes"
        fake_cache_dir.mkdir()
        monkeypatch.setattr(ix_mod, "_CACHE_DIR", fake_cache_dir)

        cache_file = fake_cache_dir / "sp500.json"
        cache_file.write_text(json.dumps(["OLD"]))
        # Make the file appear 25 hours old
        old_mtime = time.time() - (25 * 3600)
        import os
        os.utime(cache_file, (old_mtime, old_mtime))

        fresh = ["AAPL", "MSFT"]
        import pandas as pd

        def fake_read_html(url, *args, **kwargs):
            return [pd.DataFrame({"Symbol": fresh})]

        monkeypatch.setattr("pandas.read_html", fake_read_html)

        result = ix_mod.get_sp500_tickers()
        assert result == sorted(fresh)


# ---------------------------------------------------------------------------
# Task 7 — Asset class universe resolvers
# ---------------------------------------------------------------------------

class TestCryptoTickers:
    def test_returns_20_tickers(self):
        result = get_crypto_tickers()
        assert len(result) == 20

    def test_all_end_with_usd(self):
        result = get_crypto_tickers()
        for ticker in result:
            assert ticker.endswith("-USD"), f"{ticker!r} does not end with -USD"

    def test_contains_major_cryptos(self):
        result = get_crypto_tickers()
        for ticker in ("BTC-USD", "ETH-USD", "SOL-USD"):
            assert ticker in result

    def test_returns_list_of_strings(self):
        result = get_crypto_tickers()
        assert isinstance(result, list)
        assert all(isinstance(t, str) for t in result)


class TestForexTickers:
    def test_returns_at_least_8_pairs(self):
        result = get_forex_tickers()
        assert len(result) >= 8

    def test_all_end_with_x(self):
        result = get_forex_tickers()
        for ticker in result:
            assert ticker.endswith("=X"), f"{ticker!r} does not end with =X"

    def test_contains_major_pairs(self):
        result = get_forex_tickers()
        for ticker in ("EURUSD=X", "GBPUSD=X", "USDJPY=X"):
            assert ticker in result

    def test_returns_list_of_strings(self):
        result = get_forex_tickers()
        assert isinstance(result, list)
        assert all(isinstance(t, str) for t in result)


class TestMetalsTickers:
    def test_returns_list_of_strings(self):
        result = get_metals_tickers()
        assert isinstance(result, list)
        assert all(isinstance(t, str) for t in result)

    def test_contains_expected_tickers(self):
        result = get_metals_tickers()
        for ticker in ("GC=F", "SI=F", "PL=F", "CL=F", "HG=F", "GLD", "SLV", "USO"):
            assert ticker in result, f"Expected {ticker!r} in metals list"

    def test_returns_8_tickers(self):
        result = get_metals_tickers()
        assert len(result) == 8


# ---------------------------------------------------------------------------
# Task 8 — Alternative data universe resolvers
# ---------------------------------------------------------------------------

class TestCongressionalTickers:
    def test_returns_list_of_strings(self):
        result = get_congressional_tickers()
        assert isinstance(result, list)
        assert len(result) >= 25
        assert all(isinstance(t, str) for t in result)

    def test_contains_known_members(self):
        result = get_congressional_tickers()
        for ticker in ("AAPL", "MSFT", "NVDA"):
            assert ticker in result

    def test_no_dots_in_tickers(self):
        result = get_congressional_tickers()
        for ticker in result:
            assert "." not in ticker

    def test_result_is_sorted(self):
        result = get_congressional_tickers()
        assert result == sorted(result)

    def test_cache_is_written(self, tmp_path, monkeypatch):
        import engine.universes.alternative as alt_mod

        fake_dir = tmp_path / "universes"
        monkeypatch.setattr(alt_mod, "_CACHE_DIR", fake_dir)
        # Clear any prior cache reference
        cache_path = fake_dir / "congressional.json"
        assert not cache_path.exists()

        result = alt_mod.get_congressional_tickers()
        assert cache_path.exists()
        cached = json.loads(cache_path.read_text())
        assert cached == result


class TestInsiderTickers:
    def test_returns_list_of_strings(self):
        result = get_insider_tickers()
        assert isinstance(result, list)
        assert len(result) >= 20
        assert all(isinstance(t, str) for t in result)

    def test_contains_known_members(self):
        result = get_insider_tickers()
        for ticker in ("AAPL", "MSFT", "JPM"):
            assert ticker in result

    def test_no_dots_in_tickers(self):
        result = get_insider_tickers()
        for ticker in result:
            assert "." not in ticker

    def test_result_is_sorted(self):
        result = get_insider_tickers()
        assert result == sorted(result)


class TestHighShortTickers:
    def test_returns_list_of_strings(self):
        result = get_high_short_tickers()
        assert isinstance(result, list)
        assert len(result) >= 20
        assert all(isinstance(t, str) for t in result)

    def test_contains_known_meme_stocks(self):
        result = get_high_short_tickers()
        for ticker in ("GME", "AMC"):
            assert ticker in result

    def test_result_is_sorted(self):
        result = get_high_short_tickers()
        assert result == sorted(result)
