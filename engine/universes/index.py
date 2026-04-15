"""Index universe resolvers — S&P 500, Dow 30, Nasdaq 100.

All resolvers use a 24-hour file cache under data/cache/universes/.
Wikipedia tables are fetched via pd.read_html().
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CACHE_DIR = _REPO_ROOT / "data" / "cache" / "universes"
_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _read_cache(path: Path) -> list[str] | None:
    """Return cached tickers if the cache file exists and is < 24 h old."""
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age >= _CACHE_TTL_SECONDS:
        return None
    with path.open() as f:
        return json.load(f)


def _write_cache(path: Path, tickers: list[str]) -> None:
    """Write tickers to the cache file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(tickers, f)


def _normalise(ticker: str) -> str:
    """Replace dots with hyphens for yfinance compatibility (BRK.B → BRK-B)."""
    return ticker.replace(".", "-").strip()


# ---------------------------------------------------------------------------
# S&P 500
# ---------------------------------------------------------------------------

def get_sp500_tickers() -> list[str]:
    """Return a sorted list of S&P 500 constituent tickers.

    Source: Wikipedia list of S&P 500 companies.
    Results are cached for 24 hours in data/cache/universes/sp500.json.
    """
    cache_path = _CACHE_DIR / "sp500.json"
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    tables = pd.read_html(url)

    tickers: list[str] | None = None
    for table in tables:
        cols = [str(c) for c in table.columns]
        if "Symbol" in cols:
            tickers = [_normalise(str(t)) for t in table["Symbol"].tolist()]
            break

    if tickers is None:
        raise RuntimeError("Could not find 'Symbol' column in S&P 500 Wikipedia tables")

    result = sorted(tickers)
    _write_cache(cache_path, result)
    return result


# ---------------------------------------------------------------------------
# Dow 30
# ---------------------------------------------------------------------------

def get_dow30_tickers() -> list[str]:
    """Return a sorted list of Dow Jones Industrial Average constituent tickers.

    Source: Wikipedia Dow Jones Industrial Average page.
    Results are cached for 24 hours in data/cache/universes/dow30.json.
    """
    cache_path = _CACHE_DIR / "dow30.json"
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached

    url = "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average"
    tables = pd.read_html(url)

    tickers: list[str] | None = None
    for table in tables:
        cols = [str(c) for c in table.columns]
        if "Symbol" in cols:
            raw = table["Symbol"].dropna().tolist()
            # Drop any header-like values that snuck through
            raw = [str(t) for t in raw if str(t) != "Symbol"]
            tickers = sorted({_normalise(t) for t in raw if t})
            break

    if tickers is None:
        raise RuntimeError("Could not find 'Symbol' column in Dow 30 Wikipedia tables")

    _write_cache(cache_path, tickers)
    return tickers


# ---------------------------------------------------------------------------
# Nasdaq 100
# ---------------------------------------------------------------------------

def get_nasdaq100_tickers() -> list[str]:
    """Return a sorted list of Nasdaq-100 constituent tickers.

    Source: Wikipedia Nasdaq-100 page.
    Results are cached for 24 hours in data/cache/universes/nasdaq100.json.
    """
    cache_path = _CACHE_DIR / "nasdaq100.json"
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached

    url = "https://en.wikipedia.org/wiki/Nasdaq-100"
    tables = pd.read_html(url)

    tickers: list[str] | None = None
    for table in tables:
        cols = [str(c) for c in table.columns]
        if "Ticker" in cols:
            raw = table["Ticker"].dropna().tolist()
            raw = [str(t) for t in raw if str(t) not in ("Ticker", "nan")]
            tickers = sorted({_normalise(t) for t in raw if t})
            break

    if tickers is None:
        # Fallback: try "Symbol" column (Wikipedia occasionally restructures the page)
        for table in tables:
            cols = [str(c) for c in table.columns]
            if "Symbol" in cols:
                raw = table["Symbol"].dropna().tolist()
                raw = [str(t) for t in raw if str(t) not in ("Symbol", "nan")]
                tickers = sorted({_normalise(t) for t in raw if t})
                break

    if tickers is None:
        raise RuntimeError(
            "Could not find 'Ticker' or 'Symbol' column in Nasdaq-100 Wikipedia tables"
        )

    _write_cache(cache_path, tickers)
    return tickers
