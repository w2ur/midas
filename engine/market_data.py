"""Market data fetcher — yfinance wrapper with optional disk caching."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Benchmark ticker mapping
# ---------------------------------------------------------------------------

BENCHMARK_TICKERS: dict[str, str] = {
    "sp500": "^GSPC",
    "msci_world": "URTH",
    "gold": "GC=F",
    "btc": "BTC-USD",
}


class MarketDataFetcher:
    """Fetches prices, dividends, and benchmarks via yfinance with disk caching.

    Parameters
    ----------
    cache_dir:
        Optional directory for parquet-based query caching. When provided,
        identical queries are served from disk on subsequent calls.
    """

    def __init__(self, cache_dir: Optional[str | Path] = None) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        if self._cache_dir is not None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_prices(
        self,
        tickers: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """Fetch adjusted close prices for the given tickers.

        Returns a DataFrame with dates as index and tickers as columns.
        Handles both single-ticker and multi-ticker yfinance responses.
        """
        cache_key = self._make_cache_key("prices", tickers=sorted(tickers), start=str(start), end=str(end))
        cached = self._load_cache(cache_key)
        if cached is not None:
            return cached

        raw = yf.download(
            tickers,
            start=str(start),
            end=str(end),
            auto_adjust=True,
            progress=False,
        )

        # yfinance always returns MultiIndex columns regardless of ticker count.
        # Level 0 is price type (Close, Open, …), level 1 is ticker symbol.
        df = raw["Close"]
        df = self._normalize_index(df)

        self._save_cache(cache_key, df)
        return df

    def fetch_benchmarks(self, start: date, end: date) -> pd.DataFrame:
        """Fetch all four benchmark assets and return with friendly column names.

        Columns: sp500, msci_world, gold, btc
        """
        tickers = list(BENCHMARK_TICKERS.values())
        cache_key = self._make_cache_key("benchmarks", start=str(start), end=str(end))
        cached = self._load_cache(cache_key)
        if cached is not None:
            return cached

        raw = yf.download(
            tickers,
            start=str(start),
            end=str(end),
            auto_adjust=True,
            progress=False,
        )

        # Multiple tickers always → MultiIndex
        close = raw["Close"]

        # Rename yfinance tickers to friendly names
        reverse_map = {v: k for k, v in BENCHMARK_TICKERS.items()}
        df = close.rename(columns=reverse_map)[list(BENCHMARK_TICKERS.keys())]
        df = self._normalize_index(df)

        self._save_cache(cache_key, df)
        return df

    def fetch_dividends(self, ticker: str, start: date, end: date) -> pd.Series:
        """Fetch dividend history for a single ticker filtered to the date range.

        Returns a pd.Series with timezone-naive DatetimeIndex.
        """
        raw_divs = yf.Ticker(ticker).dividends

        # yfinance may return a DataFrame with a "Dividends" column — extract it as a Series
        if isinstance(raw_divs, pd.DataFrame):
            divs: pd.Series = raw_divs["Dividends"]
        else:
            divs = raw_divs

        # Strip timezone info so comparisons work consistently
        if divs.index.tz is not None:
            divs.index = divs.index.tz_localize(None)

        mask = (divs.index.date >= start) & (divs.index.date <= end)
        return divs.loc[mask]

    def fetch_current_prices(self, tickers: list[str]) -> dict[str, float]:
        """Fetch the most recent closing price for each ticker.

        Uses a 5-day window so weekend/holiday gaps don't produce empty results.
        Returns dict mapping ticker → float price.
        """
        raw = yf.download(
            tickers,
            period="5d",
            auto_adjust=True,
            progress=False,
        )

        # yfinance always returns MultiIndex columns — Close is a DataFrame with ticker columns
        close = raw["Close"]
        return {ticker: float(close[ticker].dropna().iloc[-1]) for ticker in tickers}

    # ------------------------------------------------------------------
    # Caching helpers
    # ------------------------------------------------------------------

    def _normalize_index(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize DatetimeIndex to second resolution for consistent roundtrips."""
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.copy()
            df.index = df.index.as_unit("s")
        return df

    def _make_cache_key(self, prefix: str, **kwargs) -> str:
        """Build a deterministic MD5 cache key from the query parameters."""
        payload = json.dumps({"prefix": prefix, **kwargs}, sort_keys=True)
        digest = hashlib.md5(payload.encode()).hexdigest()
        return digest

    def _cache_path(self, key: str) -> Path:
        assert self._cache_dir is not None
        return self._cache_dir / f"{key}.parquet"

    def _load_cache(self, key: str) -> Optional[pd.DataFrame]:
        if self._cache_dir is None:
            return None
        path = self._cache_path(key)
        if path.exists():
            df = pd.read_parquet(path)
            # Normalize DatetimeIndex resolution — parquet may store ms vs s
            if isinstance(df.index, pd.DatetimeIndex):
                df.index = df.index.as_unit("s")
            return df
        return None

    def _save_cache(self, key: str, df: pd.DataFrame) -> None:
        if self._cache_dir is None:
            return
        df.to_parquet(self._cache_path(key))
