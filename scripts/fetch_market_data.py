"""Fetch today's market benchmark data and save to data/market/today.json.

Fetches the last 5 trading days of data for the four benchmarks
(S&P 500, MSCI World, Gold, BTC) and saves the most recent values.

Usage:
    python scripts/fetch_market_data.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

# Add project root to sys.path so engine imports work when run directly.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from engine.market_data import MarketDataFetcher


def fetch_and_save(output_path: Path | None = None) -> dict:
    """Fetch the latest benchmark values and persist to disk.

    Parameters
    ----------
    output_path:
        Destination file. Defaults to data/market/today.json relative to
        the project root.

    Returns
    -------
    dict
        The saved payload: {"date": "YYYY-MM-DD", "benchmarks": {...}}
    """
    if output_path is None:
        output_path = _PROJECT_ROOT / "data" / "market" / "today.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    today = date.today()
    # Request the last 5 trading days so we always get at least one close.
    start = today - timedelta(days=7)

    cache_dir = _PROJECT_ROOT / "data" / "cache"
    fetcher = MarketDataFetcher(cache_dir=cache_dir)

    df = fetcher.fetch_benchmarks(start=start, end=today)

    if df.empty:
        raise RuntimeError("No benchmark data returned — market may be closed or network unavailable.")

    # Use the most recent row available.
    latest_row = df.iloc[-1]
    latest_date = df.index[-1].date()

    payload = {
        "date": latest_date.isoformat(),
        "benchmarks": {
            "sp500": round(float(latest_row["sp500"]), 2),
            "msci_world": round(float(latest_row["msci_world"]), 4),
            "gold": round(float(latest_row["gold"]), 2),
            "btc": round(float(latest_row["btc"]), 2),
        },
    }

    with output_path.open("w") as f:
        json.dump(payload, f, indent=2)

    print(f"Market data saved to {output_path}")
    print(f"  Date:       {payload['date']}")
    print(f"  S&P 500:    {payload['benchmarks']['sp500']}")
    print(f"  MSCI World: {payload['benchmarks']['msci_world']}")
    print(f"  Gold:       {payload['benchmarks']['gold']}")
    print(f"  BTC:        {payload['benchmarks']['btc']}")

    return payload


if __name__ == "__main__":
    fetch_and_save()
