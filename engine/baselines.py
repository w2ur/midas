"""Per-agent benchmark + coin-flip phantom competitors.

Data model: each baseline is a list of daily snapshots
{date, portfolio_value, cash, positions_value, currency} mirroring
the shape of data/portfolios/<agent>/snapshots.json so the site can
consume baselines with minimal new code.

Ticker choices:
- VGK  (Vanguard FTSE Europe ETF, USD-listed) replaces IMEU.L / IWDA.L UCITS
  variants which are not reliably available via yfinance. VGK tracks FTSE
  Developed Europe, consistent with engine/market_data.py conventions.
- URTH (iShares MSCI World ETF, USD-listed) replaces IWDA.L for world / global
  reference. URTH is the same proxy already used for msci_world in
  engine/market_data.py BENCHMARK_TICKERS.
Currency label is informational only — charts normalize to % return.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator, Literal

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINES_DIR = PROJECT_ROOT / "data" / "baselines"
OHLCV_DIR = PROJECT_ROOT / "data" / "market" / "ohlcv"

DAY_ONE = date(2026, 4, 17)

Currency = Literal["EUR", "USD"]


@dataclass(frozen=True)
class BenchmarkSpec:
    label: str  # Human-readable, e.g. "FTSE Europe"
    ticker: str  # Concrete OHLCV-store ticker, e.g. "VGK"
    currency: Currency  # Currency the resulting series is denominated in


# Single source of truth. Ticker choices must exist in data/market/ohlcv/.
# "EUR_CASH_FLAT" is a sentinel handled by compute_passive_benchmark —
# produces a flat €10k series (honest benchmark for monsieur-forex).
AGENT_BENCHMARKS: dict[str, BenchmarkSpec] = {
    "satoshi": BenchmarkSpec("BTC-EUR", "BTC-EUR", "EUR"),
    "yolo-sapiens-eur": BenchmarkSpec("FTSE Europe", "VGK", "USD"),
    "yolo-sapiens-usd": BenchmarkSpec("S&P 500", "SPY", "USD"),
    "goldfinger": BenchmarkSpec("Gold", "4GLD.DE", "EUR"),
    "monsieur-forex": BenchmarkSpec("EUR cash", "EUR_CASH_FLAT", "EUR"),
    "sharp-shooter-eur": BenchmarkSpec("FTSE Europe", "VGK", "USD"),
    "sharp-shooter-usd": BenchmarkSpec("S&P 500", "SPY", "USD"),
    "steady-eddie-eur": BenchmarkSpec("FTSE Europe", "VGK", "USD"),
    "steady-eddie-usd": BenchmarkSpec("S&P 500", "SPY", "USD"),
    "world": BenchmarkSpec("MSCI World", "URTH", "USD"),
}

GLOBAL_REFERENCE = BenchmarkSpec("MSCI World", "URTH", "USD")

INITIAL = 10000.0


def _daterange(start: date, end: date) -> Iterator[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _load_ohlcv(ticker: str) -> dict[str, float]:
    """Return date_iso -> close for the ticker, empty if file missing."""
    path = OHLCV_DIR / f"{ticker}.jsonl"
    if not path.exists():
        return {}
    out: dict[str, float] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        out[row["date"]] = float(row.get("adj_close") or row["close"])
    return out


def compute_passive_benchmark(
    spec: BenchmarkSpec,
    from_date: date,
    to_date: date,
) -> list[dict]:
    """€10k (or $10k) buy-and-hold of spec.ticker from from_date to to_date inclusive.

    Non-trading days carry the last observed close. Missing OHLCV data returns
    an empty list (caller treats as "no line to draw").
    """
    if spec.ticker == "EUR_CASH_FLAT":
        return [
            {
                "date": d.isoformat(),
                "portfolio_value": INITIAL,
                "cash": INITIAL,
                "positions_value": 0.0,
                "currency": spec.currency,
            }
            for d in _daterange(from_date, to_date)
        ]

    closes = _load_ohlcv(spec.ticker)
    if not closes:
        return []

    first_close: float | None = None
    last_close: float | None = None
    out: list[dict] = []
    for d in _daterange(from_date, to_date):
        iso = d.isoformat()
        if iso in closes:
            last_close = closes[iso]
            if first_close is None:
                first_close = last_close
        if first_close is None or last_close is None:
            continue  # no data yet for the range
        value = INITIAL * (last_close / first_close)
        out.append(
            {
                "date": iso,
                "portfolio_value": value,
                "cash": 0.0,
                "positions_value": value,
                "currency": spec.currency,
            }
        )
    return out
