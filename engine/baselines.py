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

Currency is the DISPLAY currency for the series (matches the agent's home
currency). The price ratio used to compute daily value is currency-invariant,
so the ETF's actual trading currency (USD for VGK/URTH) is not relevant to
the comparison. FX-noise over the short observation window is accepted as
de minimis, matching the existing snapshot-benchmark pattern in the site.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator, Literal

import pandas as pd

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
# Currency is the DISPLAY currency for the series (matches the agent's home
# currency). The price ratio used to compute daily value is currency-invariant,
# so the ETF's actual trading currency (USD for VGK/URTH) is not relevant to
# the comparison. FX-noise over the short observation window is accepted as
# de minimis, matching the existing snapshot-benchmark pattern in the site.
AGENT_BENCHMARKS: dict[str, BenchmarkSpec] = {
    "satoshi": BenchmarkSpec("BTC-EUR", "BTC-EUR", "EUR"),
    "yolo-sapiens-eur": BenchmarkSpec("FTSE Europe", "VGK", "EUR"),
    "yolo-sapiens-usd": BenchmarkSpec("S&P 500", "SPY", "USD"),
    "goldfinger": BenchmarkSpec("Gold", "4GLD.DE", "EUR"),
    "monsieur-forex": BenchmarkSpec("EUR cash", "EUR_CASH_FLAT", "EUR"),
    "sharp-shooter-eur": BenchmarkSpec("FTSE Europe", "VGK", "EUR"),
    "sharp-shooter-usd": BenchmarkSpec("S&P 500", "SPY", "USD"),
    "steady-eddie-eur": BenchmarkSpec("FTSE Europe", "VGK", "EUR"),
    "steady-eddie-usd": BenchmarkSpec("S&P 500", "SPY", "USD"),
    "world": BenchmarkSpec("MSCI World", "URTH", "EUR"),
}

GLOBAL_REFERENCE = BenchmarkSpec("MSCI World", "URTH", "EUR")

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


def _load_price_frame(
    tickers: list[str], from_date: date, to_date: date
) -> pd.DataFrame:
    """Build a DataFrame of daily closes over the date range for the given tickers.

    Missing rows are forward-filled; tickers with no file at all are dropped.
    """
    series_by_ticker: dict[str, pd.Series] = {}
    for t in tickers:
        closes = _load_ohlcv(t)
        if not closes:
            continue
        s = pd.Series({pd.Timestamp(d): v for d, v in closes.items()}).sort_index()
        series_by_ticker[t] = s
    if not series_by_ticker:
        return pd.DataFrame()
    df = pd.DataFrame(series_by_ticker)
    idx = pd.date_range(from_date, to_date, freq="D")
    return df.reindex(idx).ffill()


def compute_coin_flip(
    agent_id: str,
    tickers: list[str],
    currency: Currency,
    max_positions: int,
    from_date: date,
    to_date: date,
) -> list[dict]:
    """Random-trader-in-same-universe, €10k or $10k start, deterministic per agent.

    Builds the bt pipeline directly to avoid build_bt_strategy's StatTotalReturn
    + SelectN insertion, which would override the seeded picks with return-rank
    ordering. The seeded selector already caps picks at max_positions so no
    SelectN step is needed; LimitWeights stays as a safety valve for days when
    the available universe (after dropna) is smaller than max_positions, which
    would otherwise let WeighEqually allocate >1/max_positions to a single name.
    """
    import bt as _bt

    from engine.selectors.random_seeded import SelectRandomlySeeded, make_seed

    price_data = _load_price_frame(tickers, from_date, to_date)
    if price_data.empty:
        return []

    seed = make_seed(agent_id, from_date.isoformat())
    strategy_id = f"coinflip-{agent_id}"
    max_weight = 1.0 / max(max_positions, 1)

    pipeline = [
        _bt.algos.RunDaily(),
        SelectRandomlySeeded(n=max_positions, seed=seed),
        _bt.algos.WeighEqually(),
        _bt.algos.LimitWeights(max_weight),
        _bt.algos.Rebalance(),
    ]
    strategy = _bt.Strategy(strategy_id, pipeline)
    backtest = _bt.Backtest(strategy, price_data, initial_capital=INITIAL)
    bt_result = _bt.run(backtest)

    daily_values: pd.Series = bt_result.backtests[strategy_id].strategy.values
    snaps = [
        {"date": idx.date().isoformat(), "portfolioValue": float(val)}
        for idx, val in daily_values.items()
    ]
    return [
        {
            "date": s["date"],
            "portfolio_value": float(s["portfolioValue"]),
            "cash": 0.0,
            "positions_value": float(s["portfolioValue"]),
            "currency": currency,
        }
        for s in snaps
        if from_date.isoformat() <= s["date"] <= to_date.isoformat()
    ]
