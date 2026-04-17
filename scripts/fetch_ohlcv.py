"""Fetch historical OHLCV for every symbol any strategy or portfolio might reference.

Runs in a trusted environment (local dev or GitHub Actions) where yfinance
works reliably. Output lives at data/market/ohlcv/{SYMBOL}.jsonl — one row per
trading day, append-only, committed to git so sandboxed agents can read it.

Not to be confused with scripts/fetch_market_data.py, which writes a single
benchmark snapshot for the daily session dashboard.

Usage:
    python scripts/fetch_ohlcv.py
    python scripts/fetch_ohlcv.py --history-days 60     # short refresh
    python scripts/fetch_ohlcv.py --symbols AAPL,MSFT   # targeted
    python scripts/fetch_ohlcv.py --dry-run             # list resolved symbols
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
import yfinance as yf

from engine.universes.index import (
    get_sp500_tickers,
    get_dow30_tickers,
    get_nasdaq100_tickers,
    get_cac40_tickers,
    get_dax_tickers,
    get_ftse100_tickers,
    get_stoxx600_tickers,
)
from engine.universes.alternative import (
    get_congressional_tickers,
    get_insider_tickers,
    get_high_short_tickers,
)
from engine.universes.assets import (
    get_crypto_tickers,
    get_crypto_eur_tickers,
    get_forex_tickers,
    get_metals_tickers,
    get_voo_only,
    get_classic_60_40,
    get_bearish_etf_tickers,
    get_bearish_etf_ucits_tickers,
    get_commodities_eur_tickers,
)

_OHLCV_DIR = _PROJECT_ROOT / "data" / "market" / "ohlcv"
_PORTFOLIOS_DIR = _PROJECT_ROOT / "data" / "portfolios"

# Reference symbols always fetched — used for market commentary and regime detection
# even when no strategy directly references them.
_MARKET_CONTEXT = [
    "SPY", "QQQ", "IWM", "DIA",   # Broad indices (ETFs)
    "^VIX",                        # Volatility
    "GLD", "SLV", "TLT",           # Risk-off / safe-haven
    "BTC-USD", "ETH-USD",          # Crypto reference
    "DX-Y.NYB",                    # US Dollar Index
]

# Static universes not covered by their own resolver.
_ETF_SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB"]
_ETF_BROAD = ["VOO", "QQQ", "VEA", "VWO", "GLD", "BND", "TLT", "IWM", "DIA", "HYG"]


def _collect_holdings() -> set[str]:
    """Return every ticker currently held across all portfolios."""
    holdings: set[str] = set()
    if not _PORTFOLIOS_DIR.exists():
        return holdings
    for portfolio_dir in _PORTFOLIOS_DIR.iterdir():
        portfolio_file = portfolio_dir / "portfolio.json"
        if not portfolio_file.exists():
            continue
        with portfolio_file.open() as f:
            data = json.load(f)
        for position in data.get("positions", []):
            ticker = position.get("ticker")
            if ticker:
                holdings.add(ticker)
    return holdings


def _collect_universe_symbols() -> set[str]:
    """Union of every ticker across every declared universe resolver."""
    symbols: set[str] = set()
    resolvers = [
        get_sp500_tickers,
        get_dow30_tickers,
        get_nasdaq100_tickers,
        get_cac40_tickers,
        get_dax_tickers,
        get_ftse100_tickers,
        get_stoxx600_tickers,
        get_crypto_tickers,
        get_crypto_eur_tickers,
        get_forex_tickers,
        get_metals_tickers,
        get_voo_only,
        get_classic_60_40,
        get_bearish_etf_tickers,
        get_bearish_etf_ucits_tickers,
        get_commodities_eur_tickers,
        get_congressional_tickers,
        get_insider_tickers,
        get_high_short_tickers,
    ]
    for resolver in resolvers:
        try:
            symbols.update(resolver())
        except Exception as exc:
            print(f"  ! {resolver.__name__} failed: {exc}", file=sys.stderr)
    symbols.update(_ETF_SECTORS)
    symbols.update(_ETF_BROAD)
    return symbols


def _all_symbols() -> list[str]:
    universe = _collect_universe_symbols()
    holdings = _collect_holdings()
    context = set(_MARKET_CONTEXT)
    return sorted(universe | holdings | context)


def _existing_dates(path: Path) -> set[str]:
    if not path.exists():
        return set()
    dates: set[str] = set()
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            d = row.get("date")
            if d:
                dates.add(d)
    return dates


def _fetch_symbol(symbol: str, start: date, end: date) -> pd.DataFrame | None:
    """Fetch OHLCV for a single symbol. Returns None on failure."""
    try:
        df = yf.download(
            symbol,
            start=str(start),
            end=str(end + timedelta(days=1)),  # yfinance end is exclusive
            auto_adjust=False,                 # keep raw Close + Adj Close separately
            progress=False,
            threads=False,
        )
    except Exception as exc:
        print(f"  ! {symbol}: download error — {exc}", file=sys.stderr)
        return None
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def _safe_float(v) -> float | None:
    """Coerce a DataFrame cell to float, defending against accidental Series values."""
    if v is None:
        return None
    if isinstance(v, pd.Series):
        if v.empty:
            return None
        v = v.iloc[0]
    if pd.isna(v):
        return None
    return float(v)


def _safe_int(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, pd.Series):
        if v.empty:
            return None
        v = v.iloc[0]
    if pd.isna(v):
        return None
    return int(v)


def _write_rows(symbol: str, df: pd.DataFrame) -> int:
    """Append new daily rows to data/market/ohlcv/{SYMBOL}.jsonl."""
    path = _OHLCV_DIR / f"{symbol}.jsonl"
    existing = _existing_dates(path)

    path.parent.mkdir(parents=True, exist_ok=True)
    rows_to_append: list[tuple[str, str]] = []
    for ts, row in df.iterrows():
        d = ts.date().isoformat() if hasattr(ts, "date") else str(ts)
        if d in existing:
            continue
        record = {
            "date": d,
            "open": _safe_float(row.get("Open")),
            "high": _safe_float(row.get("High")),
            "low": _safe_float(row.get("Low")),
            "close": _safe_float(row.get("Close")),
            "adj_close": _safe_float(row.get("Adj Close")),
            "volume": _safe_int(row.get("Volume")),
        }
        if record["close"] is None:
            continue
        rows_to_append.append((d, json.dumps(record)))

    if rows_to_append:
        rows_to_append.sort(key=lambda pair: pair[0])
        with path.open("a") as f:
            for _, line in rows_to_append:
                f.write(line + "\n")
    return len(rows_to_append)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history-days", type=int, default=730,
        help="Days of history to fetch on first run (default 730 ≈ 2 years)"
    )
    parser.add_argument(
        "--symbols", type=str, default=None,
        help="Comma-separated override list (skip universe resolution)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List symbols without fetching"
    )
    args = parser.parse_args()

    if args.symbols:
        symbols = sorted({s.strip() for s in args.symbols.split(",") if s.strip()})
    else:
        symbols = _all_symbols()

    print(f"Resolved {len(symbols)} symbols to fetch.")
    if args.dry_run:
        for s in symbols:
            print(f"  {s}")
        return 0

    end = date.today()
    default_start = end - timedelta(days=args.history_days)

    total_new = 0
    failures = 0
    for i, symbol in enumerate(symbols, start=1):
        path = _OHLCV_DIR / f"{symbol}.jsonl"
        if path.exists():
            existing = _existing_dates(path)
            if existing:
                last = max(datetime.fromisoformat(d).date() for d in existing)
                if last >= end - timedelta(days=1):
                    continue  # Already up to date
                start = last + timedelta(days=1)
            else:
                start = default_start
        else:
            start = default_start

        df = _fetch_symbol(symbol, start, end)
        if df is None:
            failures += 1
            continue
        n = _write_rows(symbol, df)
        total_new += n
        if i % 25 == 0 or n > 0:
            print(f"  [{i}/{len(symbols)}] {symbol}: +{n} rows")

    print(f"\nDone. Wrote {total_new} new rows across {len(symbols)} symbols. {failures} failures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
