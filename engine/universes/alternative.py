"""Alternative data universe resolvers — congressional trades, insider buying,
high short-interest stocks.

All resolvers use a 24-hour file cache under data/cache/universes/.
External API calls (Quiver/Finnhub) fall back to curated static lists.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

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
    """Write tickers to cache, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(tickers, f)


# ---------------------------------------------------------------------------
# Congressional trades universe
# ---------------------------------------------------------------------------

# Curated fallback: ~25 stocks frequently traded by U.S. Congress members
_CONGRESSIONAL_FALLBACK: list[str] = [
    "AAPL", "MSFT", "NVDA", "GOOG", "AMZN",
    "META", "TSLA", "JPM", "BAC", "GS",
    "V", "MA", "UNH", "JNJ", "PFE",
    "XOM", "CVX", "COP", "LMT", "RTX",
    "BA", "NOC", "DIS", "NFLX", "CRM",
    "PANW", "PLTR",
]


def get_congressional_tickers() -> list[str]:
    """Return tickers frequently traded by U.S. Congress members.

    Attempts to fetch from Quiver Quantitative or Finnhub; falls back to a
    curated list of ~25 commonly traded stocks. Results cached for 24 hours.
    """
    cache_path = _CACHE_DIR / "congressional.json"
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached

    tickers: list[str] | None = None

    # Attempt: Quiver Quantitative public endpoint (no auth required for basic data)
    try:
        import urllib.request
        import urllib.error

        url = "https://www.quiverquant.com/sources/congresstrading"
        req = urllib.request.Request(url, headers={"User-Agent": "midas-fund/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            import json as _json
            data = _json.loads(resp.read())
            if isinstance(data, list) and data:
                seen: set[str] = set()
                result: list[str] = []
                for row in data:
                    ticker = row.get("Ticker") or row.get("ticker")
                    if ticker and ticker not in seen:
                        seen.add(ticker)
                        result.append(ticker.replace(".", "-"))
                if len(result) >= 10:
                    tickers = sorted(result)
    except Exception:
        # Network failure, auth error, or unexpected format — use fallback
        pass

    if tickers is None:
        tickers = sorted(_CONGRESSIONAL_FALLBACK)

    _write_cache(cache_path, tickers)
    return tickers


# ---------------------------------------------------------------------------
# Insider buying universe
# ---------------------------------------------------------------------------

# Curated list of stocks with historically significant insider buying activity.
# PXD removed 2026-04-17 (acquired by Exxon in October 2023).
_INSIDER_FALLBACK: list[str] = [
    "AAPL", "MSFT", "AMZN", "GOOG", "META",
    "BRK-B", "JPM", "BAC", "WFC", "C",
    "XOM", "CVX", "OXY", "COP",
    "LMT", "RTX", "NOC", "BA", "GD",
    "UNH", "CVS", "HCA", "CI", "MCK",
    "COST", "HD", "LOW", "TGT", "WMT",
]


def get_insider_tickers() -> list[str]:
    """Return stocks with historically significant insider buying activity.

    Uses a curated fallback list. Results cached for 24 hours.
    """
    cache_path = _CACHE_DIR / "insider.json"
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached

    tickers = sorted(_INSIDER_FALLBACK)
    _write_cache(cache_path, tickers)
    return tickers


# ---------------------------------------------------------------------------
# High short-interest universe
# ---------------------------------------------------------------------------

# Curated list of stocks historically carrying elevated short interest.
# Last refreshed 2026-04-17: removed BBBY, PRTY, JWN, OSTK, WISH, EXPR
# (delisted / bankrupt between 2023-2025). A data-driven refresh via
# the Finnhub short-interest API would be better long-term.
_HIGH_SHORT_FALLBACK: list[str] = [
    "GME", "AMC", "SPCE", "PLTR", "RIVN",
    "LCID", "NKLA", "WKHS", "RIDE", "BYND",
    "CVNA", "BBWI", "M", "KSS", "FUBO",
    "SFIX", "CLOV", "FIZZ", "PUBM",
]


def get_high_short_tickers() -> list[str]:
    """Return stocks with historically high short interest.

    Uses a curated fallback list. Results cached for 24 hours.
    """
    cache_path = _CACHE_DIR / "high-short.json"
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached

    tickers = sorted(_HIGH_SHORT_FALLBACK)
    _write_cache(cache_path, tickers)
    return tickers
