"""Tests for universe resolvers — index, asset class, and alternative data.

Tests must NEVER write to the real `data/universes/` directory. Every test
that exercises the cache-write path monkeypatches `_DATA_DIR` to a tmp_path.
A previous version of this file silently overwrote the real `sp500.json`
during pytest runs, dropping it from 503 tickers to 3 — the Apr 29 cloud
session aborted as a downstream consequence.
"""

from __future__ import annotations

import io
import json

from engine.config import get_config
from pathlib import Path

import pytest

from engine.universes.alternative import (
    get_congressional_tickers,
    get_high_short_tickers,
    get_insider_tickers,
)
from engine.universes.assets import (
    get_classic_60_40,
    get_crypto_tickers,
    get_forex_tickers,
    get_metals_tickers,
    get_voo_only,
)


# ---------------------------------------------------------------------------
# Index universe resolvers — never touch the network in tests
# ---------------------------------------------------------------------------


class TestSP500Tickers:
    def test_committed_data_present_and_valid(self):
        """The committed `data/universes/sp500.json` must contain a real S&P 500."""
        from engine.universes.index import get_sp500_tickers

        tickers = get_sp500_tickers()
        assert isinstance(tickers, list)
        # Real S&P 500 has ~500 constituents; <100 means the file is corrupt
        # (e.g. test pollution). Apr 29 incident.
        assert len(tickers) > 100, (
            f"sp500 file looks corrupt: only {len(tickers)} tickers"
        )
        assert all(isinstance(t, str) for t in tickers)

    def test_no_dots_in_committed_tickers(self):
        from engine.universes.index import get_sp500_tickers

        for ticker in get_sp500_tickers():
            assert "." not in ticker, f"{ticker!r} still contains a dot"

    def test_isolated_cache_returns_isolated_data(self, midas_data_root, monkeypatch):
        """Monkeypatch the data dir; verify reads come from the patched location."""
        import engine.universes.index as ix_mod

        fake_dir = get_config().universes_dir
        fake_dir.mkdir(parents=True, exist_ok=True)

        sample = ["AAPL", "MSFT"]
        (fake_dir / "sp500.json").write_text(json.dumps(sample))
        assert ix_mod.get_sp500_tickers() == sample

    def test_no_network_call_when_file_exists(self, midas_data_root, monkeypatch):
        import engine.universes.index as ix_mod

        fake_dir = get_config().universes_dir
        fake_dir.mkdir(parents=True, exist_ok=True)
        (fake_dir / "sp500.json").write_text(json.dumps(["AAPL"]))

        called = []

        def boom(*a, **kw):
            called.append(True)
            raise AssertionError("network must not be called when data file exists")

        monkeypatch.setattr(ix_mod, "_fetch_html_tables", boom)

        assert ix_mod.get_sp500_tickers() == ["AAPL"]
        assert not called


class TestDow30Tickers:
    def test_committed_data_present_and_valid(self):
        from engine.universes.index import get_dow30_tickers

        tickers = get_dow30_tickers()
        assert isinstance(tickers, list)
        assert len(tickers) >= 25, (
            f"dow30 file looks corrupt: only {len(tickers)} tickers"
        )

    def test_known_members_present(self):
        from engine.universes.index import get_dow30_tickers

        tickers = get_dow30_tickers()
        for t in ("AAPL", "MSFT"):
            assert t in tickers


class TestNasdaq100Tickers:
    def test_committed_data_present_and_valid(self):
        from engine.universes.index import get_nasdaq100_tickers

        tickers = get_nasdaq100_tickers()
        assert isinstance(tickers, list)
        assert len(tickers) >= 50, (
            f"nasdaq100 file looks corrupt: only {len(tickers)} tickers"
        )

    def test_known_members_present(self):
        from engine.universes.index import get_nasdaq100_tickers

        tickers = get_nasdaq100_tickers()
        for t in ("AAPL", "MSFT", "NVDA"):
            assert t in tickers


class TestEUIndices:
    def test_cac40_committed_and_paris_suffix(self):
        from engine.universes.index import get_cac40_tickers

        tickers = get_cac40_tickers()
        assert len(tickers) >= 30
        assert any(t.endswith(".PA") for t in tickers)

    def test_dax_committed(self):
        from engine.universes.index import get_dax_tickers

        assert len(get_dax_tickers()) >= 30

    def test_ftse100_all_lse_suffix(self):
        from engine.universes.index import get_ftse100_tickers

        tickers = get_ftse100_tickers()
        assert len(tickers) >= 80
        for t in tickers:
            assert t.endswith(".L"), f"{t!r} missing .L suffix"

    def test_stoxx600_committed(self):
        from engine.universes.index import get_stoxx600_tickers

        assert len(get_stoxx600_tickers()) >= 400


class TestRefreshFunctions:
    def test_refresh_sp500_writes_to_data_dir(self, midas_data_root, monkeypatch):
        import engine.universes.index as ix_mod
        import pandas as pd

        fake_dir = get_config().universes_dir
        fake_dir.mkdir(parents=True, exist_ok=True)

        fresh = [f"T{i:03d}" for i in range(150)]

        def fake_fetch(url):
            return [pd.DataFrame({"Symbol": fresh})]

        monkeypatch.setattr(ix_mod, "_fetch_html_tables", fake_fetch)

        result = ix_mod.refresh_sp500()
        assert result == sorted(fresh)
        assert (fake_dir / "sp500.json").exists()
        assert json.loads((fake_dir / "sp500.json").read_text()) == sorted(fresh)

    def test_refresh_nasdaq100_reads_slickcharts_symbol_column(
        self, midas_data_root, monkeypatch
    ):
        """Source moved to Slickcharts on 2026-07-13 (Wikipedia dropped the
        constituents table). Refresh reads the largest 'Symbol' table, ignores
        stray header rows, and writes the committed file."""
        import engine.universes.index as ix_mod
        import pandas as pd

        fake_dir = get_config().universes_dir
        fake_dir.mkdir(parents=True, exist_ok=True)
        fresh = [f"N{i:03d}" for i in range(100)]

        # Slickcharts table: a "Symbol" column plus a stray repeated header row.
        def fake_fetch(url):
            assert "slickcharts" in url
            return [pd.DataFrame({"Symbol": ["Symbol", *fresh]})]

        monkeypatch.setattr(ix_mod, "_fetch_html_tables", fake_fetch)
        result = ix_mod.refresh_nasdaq100()
        assert result == sorted(fresh)

    def test_refresh_nasdaq100_falls_back_to_ticker_column(
        self, midas_data_root, monkeypatch
    ):
        """Tolerant column detection: if Slickcharts ever renames "Symbol" to
        "Ticker" (a real-world rename its Nasdaq-100 sibling pages already
        use), the refresher must still find the tickers instead of raising."""
        import engine.universes.index as ix_mod
        import pandas as pd

        fake_dir = get_config().universes_dir
        fake_dir.mkdir(parents=True, exist_ok=True)
        fresh = [f"N{i:03d}" for i in range(100)]

        def fake_fetch(url):
            # No "Symbol" column at all — only the renamed "Ticker" column.
            return [pd.DataFrame({"Company": fresh, "Ticker": fresh})]

        monkeypatch.setattr(ix_mod, "_fetch_html_tables", fake_fetch)
        result = ix_mod.refresh_nasdaq100()
        assert result == sorted(fresh)

    def test_refresh_nasdaq100_raises_when_no_known_column(
        self, midas_data_root, monkeypatch
    ):
        """Neither 'Symbol' nor 'Ticker' present — this is a genuine layout
        change the refresher cannot recover from and must surface loudly."""
        import engine.universes.index as ix_mod
        import pandas as pd

        fake_dir = get_config().universes_dir
        fake_dir.mkdir(parents=True, exist_ok=True)

        def fake_fetch(url):
            return [pd.DataFrame({"Company": ["Apple", "Microsoft"]})]

        monkeypatch.setattr(ix_mod, "_fetch_html_tables", fake_fetch)
        with pytest.raises(RuntimeError, match="Nasdaq-100"):
            ix_mod.refresh_nasdaq100()


class TestRefreshAllIndexesDegradesPerIndex:
    """The weekly refresh must not go dark for the other six indexes because
    one scraper's upstream layout changed — this is the class of bug that
    broke Nasdaq-100 on 2026-07-13 and, before the loop was hardened, would
    have aborted sp500/dow30/cac40/dax/ftse100/stoxx600 too."""

    def test_one_failing_refresher_does_not_abort_the_others(
        self, midas_data_root, monkeypatch
    ):
        import engine.universes.index as ix_mod

        def boom():
            raise RuntimeError(
                "Nasdaq-100: no 'Symbol' or 'Ticker' column on Slickcharts page"
            )

        monkeypatch.setattr(ix_mod, "refresh_nasdaq100", boom)
        monkeypatch.setattr(ix_mod, "refresh_sp500", lambda: ["AAPL", "MSFT"])
        monkeypatch.setattr(ix_mod, "refresh_dow30", lambda: ["AAPL"])
        monkeypatch.setattr(ix_mod, "refresh_cac40", lambda: ["MC.PA"])
        monkeypatch.setattr(ix_mod, "refresh_dax", lambda: ["SAP.DE"])
        monkeypatch.setattr(ix_mod, "refresh_ftse100", lambda: ["HSBA.L"])
        monkeypatch.setattr(ix_mod, "refresh_stoxx600", lambda: ["ASML.AS"])

        result = ix_mod.refresh_all_indexes()

        assert "nasdaq100" not in result
        assert result == {
            "sp500": 2,
            "dow30": 1,
            "cac40": 1,
            "dax": 1,
            "ftse100": 1,
            "stoxx600": 1,
        }

    def test_all_succeed_returns_all_seven(self, midas_data_root, monkeypatch):
        import engine.universes.index as ix_mod

        for name in (
            "refresh_sp500",
            "refresh_dow30",
            "refresh_nasdaq100",
            "refresh_cac40",
            "refresh_dax",
            "refresh_ftse100",
            "refresh_stoxx600",
        ):
            monkeypatch.setattr(ix_mod, name, lambda: ["X"])

        result = ix_mod.refresh_all_indexes()
        assert set(result) == {
            "sp500",
            "dow30",
            "nasdaq100",
            "cac40",
            "dax",
            "ftse100",
            "stoxx600",
        }
        assert all(count == 1 for count in result.values())


# ---------------------------------------------------------------------------
# Asset class universe resolvers (no I/O)
# ---------------------------------------------------------------------------


class TestCryptoTickers:
    def test_returns_20_tickers(self):
        assert len(get_crypto_tickers()) == 20

    def test_all_end_with_usd(self):
        for t in get_crypto_tickers():
            assert t.endswith("-USD")

    def test_contains_major_cryptos(self):
        result = get_crypto_tickers()
        for t in ("BTC-USD", "ETH-USD", "SOL-USD"):
            assert t in result


class TestForexTickers:
    def test_returns_at_least_8_pairs(self):
        assert len(get_forex_tickers()) >= 8

    def test_all_end_with_x(self):
        for t in get_forex_tickers():
            assert t.endswith("=X")

    def test_contains_major_pairs(self):
        result = get_forex_tickers()
        for t in ("EURUSD=X", "GBPUSD=X", "USDJPY=X"):
            assert t in result


class TestMetalsTickers:
    def test_contains_expected_tickers(self):
        result = get_metals_tickers()
        for t in ("GC=F", "SI=F", "PL=F", "CL=F", "HG=F", "GLD", "SLV", "USO"):
            assert t in result

    def test_returns_8_tickers(self):
        assert len(get_metals_tickers()) == 8


class TestVOOOnlyTickers:
    def test_returns_single_ticker(self):
        assert get_voo_only() == ["VOO"]


class TestClassic6040Tickers:
    def test_contains_voo_and_bnd(self):
        result = get_classic_60_40()
        assert "VOO" in result and "BND" in result

    def test_returns_two_tickers(self):
        assert len(get_classic_60_40()) == 2


# ---------------------------------------------------------------------------
# Alternative data universe resolvers
# ---------------------------------------------------------------------------


class TestCongressionalTickers:
    def test_committed_or_seeds_from_fallback(self):
        result = get_congressional_tickers()
        assert isinstance(result, list)
        assert len(result) >= 25
        assert "AAPL" in result and "MSFT" in result

    def test_no_dots_in_tickers(self):
        for t in get_congressional_tickers():
            assert "." not in t

    def test_result_is_sorted(self):
        result = get_congressional_tickers()
        assert result == sorted(result)

    def test_isolated_seed_writes_to_patched_dir(self, midas_data_root, monkeypatch):
        import engine.universes.alternative as alt_mod

        fake_dir = get_config().universes_dir
        fake_dir.mkdir(parents=True, exist_ok=True)
        cache_path = fake_dir / "congressional.json"
        assert not cache_path.exists()

        result = alt_mod.get_congressional_tickers()
        assert cache_path.exists()
        assert json.loads(cache_path.read_text()) == result


class TestInsiderTickers:
    def test_committed_or_seeds(self):
        result = get_insider_tickers()
        assert len(result) >= 20
        for t in ("AAPL", "MSFT", "JPM"):
            assert t in result

    def test_result_is_sorted(self):
        assert get_insider_tickers() == sorted(get_insider_tickers())


class TestHighShortTickers:
    def test_committed_or_seeds(self):
        result = get_high_short_tickers()
        # Floor lowered from 20 to 15 after 2026-04-17 delisting cleanup.
        assert len(result) >= 15

    def test_contains_known_meme_stocks(self):
        result = get_high_short_tickers()
        for t in ("GME", "AMC"):
            assert t in result

    def test_result_is_sorted(self):
        assert get_high_short_tickers() == sorted(get_high_short_tickers())


# ---------------------------------------------------------------------------
# STOXX 600 — ISIN-keyed resolution (issue #36)
# ---------------------------------------------------------------------------


def _quote(symbol: str, exchange: str, quote_type: str = "EQUITY") -> dict:
    return {"symbol": symbol, "exchange": exchange, "quoteType": quote_type}


def _row(isin: str, name: str = "Some Co", country: str = "France") -> dict[str, str]:
    return {
        "Constituent ISIN": isin,
        "Constituent Name": name,
        "Constituent Country": country,
    }


class _FakeResp(io.BytesIO):
    """`urllib.request.urlopen` stand-in: a context manager over bytes."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestStoxx600PickSymbol:
    """Regression: issue #36 — Wikipedia's ticker column produced 120 symbols
    Yahoo had no route for. Resolution is now keyed on ISIN, and these pin the
    listing-selection rule against the vendor answers measured 2026-09-09."""

    def test_home_market_suffix_beats_first_returned(self):
        from engine.universes.index import _pick_symbol

        # Yahoo listed Stuttgart's placeholder first for Inchcape.
        quotes = [_quote("GB00B61TVQ02.SG", "STU"), _quote("INCH.L", "LSE")]
        assert _pick_symbol("GB00B61TVQ02", "United Kingdom", quotes) == "INCH.L"

    def test_xetra_beats_frankfurt_floor_for_a_german_name(self):
        from engine.universes.index import _pick_symbol

        quotes = [_quote("G1A.F", "FRA"), _quote("G1A.DE", "GER")]
        assert _pick_symbol("DE0006602006", "Germany", quotes) == "G1A.DE"

    def test_frankfurt_floor_accepted_when_it_is_the_only_german_listing(self):
        from engine.universes.index import _pick_symbol

        # Fresenius' new ISIN answered only with its Frankfurt quote.
        assert _pick_symbol("DE000FRE5EN2", "Germany", [_quote("FRE.F", "FRA")]) == "FRE.F"

    def test_frankfurt_floor_refused_for_a_foreign_name(self):
        from engine.universes.index import _pick_symbol

        # SAGAX B (Stockholm) answered only as a Frankfurt regional print.
        assert _pick_symbol("SE0005127818", "Sweden", [_quote("EFE.F", "FRA")]) is None

    def test_domicile_without_a_home_listing_takes_the_real_symbol(self):
        from engine.universes.index import _pick_symbol

        # Prosus is domiciled "China" in the export; no suffix preference applies.
        quotes = [_quote("PRX.AS", "AMS"), _quote("NL0013654783.SG", "STU")]
        assert _pick_symbol("NL0013654783", "China", quotes) == "PRX.AS"

    def test_placeholder_only_is_refused(self):
        from engine.universes.index import _pick_symbol

        quotes = [_quote("GB0000000001.SG", "STU")]
        assert _pick_symbol("GB0000000001", "United Kingdom", quotes) is None

    def test_otc_and_non_equity_are_refused(self):
        from engine.universes.index import _pick_symbol

        # NMC Health: the only answer was a pink-sheet line typed MUTUALFUND.
        quotes = [_quote("NMMCF", "PNK", "MUTUALFUND")]
        assert _pick_symbol("GB00B7FC0762", "United Kingdom", quotes) is None
        assert _pick_symbol("GB00B7FC0762", "United Kingdom", []) is None

    def test_us_primary_listing_without_suffix_is_accepted(self):
        from engine.universes.index import _pick_symbol

        assert _pick_symbol("NL0015002SN0", "Netherlands", [_quote("QGEN", "NYQ")]) == "QGEN"


class TestStoxx600ResolveIsins:
    def test_maps_each_isin_and_lists_the_unresolved(self):
        from engine.universes.index import ISIN_LOOKUP_SPACING_S, resolve_isins

        answers = {
            "FR0000120073": [_quote("AI.PA", "PAR")],
            "FR0014010OO5": [],  # Air Liquide's bonus-share line
            "SE0011166610": [_quote("ATCO-A.ST", "STO")],
        }
        sleeps: list[float] = []
        rows = [
            _row("FR0000120073", "Air Liquide"),
            _row("FR0014010OO5", "L AIR LIQUIDE"),
            _row("SE0011166610", "Atlas Copco A", "Sweden"),
        ]
        resolved, unresolved = resolve_isins(
            rows, lookup=lambda isin: answers[isin], sleep=sleeps.append
        )
        assert resolved == {"FR0000120073": "AI.PA", "SE0011166610": "ATCO-A.ST"}
        assert unresolved == [("FR0014010OO5", "L AIR LIQUIDE")]
        # Throttled BETWEEN lookups: n-1 sleeps of the documented spacing.
        assert sleeps == [ISIN_LOOKUP_SPACING_S] * 2

    def test_lookup_retries_a_rate_limit_then_gives_up(self, monkeypatch):
        import sys
        import types

        import engine.universes.index as ix_mod

        class FlakyOnce:
            calls = 0

            def __init__(self, query, **kwargs):
                FlakyOnce.calls += 1
                if FlakyOnce.calls == 1:
                    raise RuntimeError("Too Many Requests. Rate limited.")
                self.quotes = [_quote("AI.PA", "PAR")]

        monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Search=FlakyOnce))
        sleeps: list[float] = []
        assert ix_mod._search_isin_quotes("FR0000120073", sleep=sleeps.append) == [
            _quote("AI.PA", "PAR")
        ]
        assert sleeps == [ix_mod.ISIN_LOOKUP_RETRY_SLEEPS_S[0]]

        class AlwaysLimited:
            def __init__(self, query, **kwargs):
                raise RuntimeError("Too Many Requests. Rate limited.")

        monkeypatch.setitem(
            sys.modules, "yfinance", types.SimpleNamespace(Search=AlwaysLimited)
        )
        sleeps.clear()
        assert ix_mod._search_isin_quotes("FR0000120073", sleep=sleeps.append) == []
        assert sleeps == list(ix_mod.ISIN_LOOKUP_RETRY_SLEEPS_S)


class TestStoxx600Constituents:
    def test_keeps_only_isin_shaped_lines(self, monkeypatch):
        import engine.universes.index as ix_mod

        csv_text = "﻿" + "\n".join(
            [
                "ShareClass ISIN;Constituent ISIN;Constituent Name;Constituent Country;"
                "Constituent Currency ISO Code;Constituent Weighting",
                "LU0328475792;NL0010273215;ASML Holding NV;Netherlands;EUR;0.04",
                "LU0328475792;_CURRENCYEUR;EURO CURRENCY;;EUR;0.001",
                "LU0328475792;___ADI2V9YR9;STOXX EUROPE 600  SEP26;Germany;EUR;0.002",
                "LU0328475792;IE00BZ3FDF20;;Ireland;EUR;0.0001",
            ]
        )
        monkeypatch.setattr(
            ix_mod.urllib.request,
            "urlopen",
            lambda req, timeout: _FakeResp(csv_text.encode("utf-8")),
        )
        rows = ix_mod._fetch_stoxx600_constituents()
        assert [r["Constituent ISIN"] for r in rows] == ["NL0010273215", "IE00BZ3FDF20"]
        assert rows[0]["Constituent Name"] == "ASML Holding NV"

    def test_missing_column_is_a_layout_change(self, monkeypatch):
        import engine.universes.index as ix_mod

        csv_text = "ShareClass ISIN;ISIN;Name\nLU0328475792;NL0010273215;ASML\n"
        monkeypatch.setattr(
            ix_mod.urllib.request,
            "urlopen",
            lambda req, timeout: _FakeResp(csv_text.encode("utf-8")),
        )
        with pytest.raises(RuntimeError, match="layout changed"):
            ix_mod._fetch_stoxx600_constituents()


class TestRefreshStoxx600:
    @staticmethod
    def _rows(n: int) -> list[dict[str, str]]:
        return [_row(f"FR{i:010d}", f"Co {i}") for i in range(n)]

    @staticmethod
    def _install(monkeypatch, rows, lookup) -> None:
        import engine.universes.index as ix_mod

        monkeypatch.setattr(ix_mod, "_fetch_stoxx600_constituents", lambda: rows)
        monkeypatch.setattr(ix_mod, "_search_isin_quotes", lookup)
        monkeypatch.setattr(ix_mod.time, "sleep", lambda s: None)

    def test_writes_sorted_deduped_symbols_and_tolerates_a_small_residual(
        self, midas_data_root, monkeypatch
    ):
        import engine.universes.index as ix_mod

        rows = self._rows(500)

        # Two ISINs answering the same symbol (a loyalty-share line that DOES
        # resolve) collapse to one; one line answers nothing.
        def lookup(isin: str) -> list[dict]:
            i = int(isin[2:])
            if i == 7:
                return []
            if i == 8:
                return [_quote("C0006.PA", "PAR")]
            return [_quote(f"C{i:04d}.PA", "PAR")]

        self._install(monkeypatch, rows, lookup)
        result = ix_mod.refresh_stoxx600()
        assert result == sorted(set(result))
        assert len(result) == 498
        assert "C0007.PA" not in result
        written = json.loads((get_config().universes_dir / "stoxx600.json").read_text())
        assert written == result

    def test_refuses_to_overwrite_when_the_vendor_lookup_is_down(
        self, midas_data_root, monkeypatch
    ):
        """A rate limit that outlasts the retry schedule, or an outage, reads as
        ~100% unresolved: the committed file must stay at its last known-good
        value rather than shrink to whatever trickled through."""
        import engine.universes.index as ix_mod

        fake_dir = get_config().universes_dir
        fake_dir.mkdir(parents=True, exist_ok=True)
        (fake_dir / "stoxx600.json").write_text(json.dumps(["KEEP.PA"]))

        rows = self._rows(500)
        unresolved_n = int(len(rows) * ix_mod.MAX_UNRESOLVED_ISIN_RATE) + 1

        def lookup(isin: str) -> list[dict]:
            i = int(isin[2:])
            return [] if i < unresolved_n else [_quote(f"C{i:04d}.PA", "PAR")]

        self._install(monkeypatch, rows, lookup)
        with pytest.raises(RuntimeError, match="unresolved"):
            ix_mod.refresh_stoxx600()
        assert json.loads((fake_dir / "stoxx600.json").read_text()) == ["KEEP.PA"]

    def test_control_the_gate_can_pass(self, midas_data_root, monkeypatch):
        """Falsifying control for the test above: one fewer unresolved line and
        the same setup writes the file."""
        import engine.universes.index as ix_mod

        rows = self._rows(500)
        unresolved_n = int(len(rows) * ix_mod.MAX_UNRESOLVED_ISIN_RATE)

        def lookup(isin: str) -> list[dict]:
            i = int(isin[2:])
            return [] if i < unresolved_n else [_quote(f"C{i:04d}.PA", "PAR")]

        self._install(monkeypatch, rows, lookup)
        assert len(ix_mod.refresh_stoxx600()) == 500 - unresolved_n

    def test_no_network_call_when_file_exists(self, midas_data_root, monkeypatch):
        import engine.universes.index as ix_mod

        fake_dir = get_config().universes_dir
        fake_dir.mkdir(parents=True, exist_ok=True)
        (fake_dir / "stoxx600.json").write_text(json.dumps(["AI.PA"]))

        def boom(*a, **kw):
            raise AssertionError("network must not be called when data file exists")

        monkeypatch.setattr(ix_mod, "_fetch_stoxx600_constituents", boom)
        monkeypatch.setattr(ix_mod, "_search_isin_quotes", boom)
        assert ix_mod.get_stoxx600_tickers() == ["AI.PA"]
