"""Index universe resolvers — S&P 500, Dow 30, Nasdaq 100.

All resolvers use a 24-hour file cache under data/cache/universes/.
Wikipedia tables are fetched via pd.read_html().
"""

from __future__ import annotations

import io
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_WIKI_USER_AGENT = "midas-fund/0.1 (https://github.com/w2ur/midas; research)"


def _fetch_wikipedia_tables(url: str) -> list[pd.DataFrame]:
    """Fetch a Wikipedia page with a descriptive User-Agent and parse its tables.

    Wikipedia rejects pandas' default Python-urllib UA, so we fetch the HTML
    ourselves before handing it to pd.read_html.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _WIKI_USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8")
    return pd.read_html(io.StringIO(html))


def _largest_table_with_column(
    tables: list[pd.DataFrame], column: str
) -> pd.DataFrame | None:
    """Return the largest table containing `column` in its columns.

    Robust against Wikipedia page layout changes: avoids picking small
    "examples" or "recent changes" tables that happen to share a column name.
    """
    candidates = [t for t in tables if column in [str(c) for c in t.columns]]
    if not candidates:
        return None
    return max(candidates, key=len)

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
    tables = _fetch_wikipedia_tables(url)

    table = _largest_table_with_column(tables, "Symbol")
    if table is None:
        raise RuntimeError("Could not find 'Symbol' column in S&P 500 Wikipedia tables")

    tickers = sorted({_normalise(str(t)) for t in table["Symbol"].tolist()})
    # Sanity check: S&P 500 must have ~500 constituents, never a handful.
    if len(tickers) < 100:
        raise RuntimeError(
            f"S&P 500 scrape returned only {len(tickers)} tickers — Wikipedia layout may have changed"
        )

    _write_cache(cache_path, tickers)
    return tickers


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
    tables = _fetch_wikipedia_tables(url)

    table = _largest_table_with_column(tables, "Symbol")
    if table is None:
        raise RuntimeError("Could not find 'Symbol' column in Dow 30 Wikipedia tables")

    raw = [str(t) for t in table["Symbol"].dropna().tolist() if str(t) != "Symbol"]
    tickers = sorted({_normalise(t) for t in raw if t})
    if len(tickers) < 20:
        raise RuntimeError(
            f"Dow 30 scrape returned only {len(tickers)} tickers — Wikipedia layout may have changed"
        )

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
    tables = _fetch_wikipedia_tables(url)

    # Wikipedia occasionally restructures the page — check both possible column names.
    table = _largest_table_with_column(tables, "Ticker") or _largest_table_with_column(tables, "Symbol")
    tickers: list[str] | None = None
    if table is not None:
        col = "Ticker" if "Ticker" in [str(c) for c in table.columns] else "Symbol"
        raw = [str(t) for t in table[col].dropna().tolist() if str(t) not in (col, "nan")]
        tickers = sorted({_normalise(t) for t in raw if t})

    if tickers is None or len(tickers) < 50:
        raise RuntimeError(
            f"Nasdaq-100 scrape returned {len(tickers) if tickers else 0} tickers — "
            "Wikipedia layout may have changed"
        )

    _write_cache(cache_path, tickers)
    return tickers
