"""Portfolio valuation — mark-to-market in native currency and EUR equivalent.

Prices positions from the committed OHLCV store (data/market/ohlcv/) and
converts to EUR via engine/fx.py. Used by daily_log for leaderboard ranking
and by the orchestrator for budget verification.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from engine.fx import to_eur

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OHLCV = _REPO_ROOT / "data" / "market" / "ohlcv"


def _latest_close(ticker: str, on: date | None = None) -> float | None:
    """Return the most recent close (or adj_close) for a ticker ≤ `on` date.

    Reads the JSONL store directly to avoid circular imports via MarketDataFetcher.
    """
    path = _OHLCV / f"{ticker}.jsonl"
    if not path.exists():
        return None
    target = on.isoformat() if on is not None else "9999-99-99"  # pick latest available
    best_date: str | None = None
    best_price: float | None = None
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
            if d is None or d > target:
                continue
            if best_date is None or d > best_date:
                best_date = d
                price = row.get("adj_close") if row.get("adj_close") is not None else row.get("close")
                best_price = float(price) if price is not None else None
    return best_price


def portfolio_mtm(portfolio_summary: dict, on: date | None = None) -> float:
    """Return the mark-to-market value of a portfolio in its native currency.

    Parameters
    ----------
    portfolio_summary:
        Dict with keys `cash`, `positions` (list of {ticker, shares} or just tickers)
        and `currency`. For list-of-tickers format, positions are assumed to be zero
        at time of valuation (used before any trades land).
    on:
        Valuation date, defaults to today.
    """
    cash = portfolio_summary.get("cash", 0.0)
    positions = portfolio_summary.get("positions", [])
    # Compatibility: positions might be a list of ticker strings or a list of dicts.
    total = cash
    for p in positions:
        if isinstance(p, dict):
            ticker = p.get("ticker")
            shares = p.get("shares", 0)
        else:
            ticker = p
            shares = 0
        if not ticker or shares == 0:
            continue
        price = _latest_close(ticker, on)
        if price is not None:
            total += shares * price
    return total


def portfolio_mtm_eur(portfolio_summary: dict, on: date | None = None) -> float | None:
    """Mark-to-market in EUR. Returns None if FX rate unavailable."""
    native = portfolio_mtm(portfolio_summary, on)
    currency = portfolio_summary.get("currency", "USD")
    if currency == "EUR":
        return native
    return to_eur(native, currency, on)
