"""Mirror-shape backtests: replay another portfolio as if you'd copied it.

v1 supports `agent:<agent_id>` sources only — the 10 Midas trading agents
whose portfolio snapshots live in data/portfolios/. Future plans add
`pelosi`, `13f-berkshire`, etc. via separate ingestion scripts.

A mirror "backtest" is just a read: snapshots.json IS the equity curve.
We filter to the requested window, normalise to the user's capital, and
compute metrics from the curve directly (no bt run).
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd  # noqa: E402

from engine.backtest import BacktestResult  # noqa: E402

from backtester.schemas import MirrorConfig

_PORTFOLIOS_DIR = _PROJECT_ROOT / "data" / "portfolios"

AGENT_IDS = {
    "satoshi",
    "yolo-sapiens-eur",
    "yolo-sapiens-usd",
    "goldfinger",
    "monsieur-forex",
    "sharp-shooter-eur",
    "sharp-shooter-usd",
    "steady-eddie-eur",
    "steady-eddie-usd",
    "world",
}


class MirrorError(ValueError):
    """Raised when a mirror config cannot be replayed."""


def _is_finite_number(v) -> bool:
    if v is None:
        return False
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return False
    return True


def _load_agent_curve(agent_id: str) -> pd.Series:
    path = _PORTFOLIOS_DIR / agent_id / "snapshots.json"
    if not path.exists():
        raise MirrorError(f"No snapshots found for agent {agent_id!r}")
    snapshots = json.loads(path.read_text())
    rows: list[tuple[pd.Timestamp, float]] = []
    for s in snapshots:
        v = s.get("portfolio_value")
        if not _is_finite_number(v):
            continue
        rows.append((pd.Timestamp(s["date"]), float(v)))
    if not rows:
        raise MirrorError(f"Agent {agent_id!r} has no usable portfolio values")
    series = pd.Series(dict(rows)).sort_index()
    return series


def _load_agent_transactions(agent_id: str) -> pd.DataFrame | None:
    path = _PORTFOLIOS_DIR / agent_id / "trades.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    if not raw:
        return None
    rows = []
    for t in raw:
        action = t.get("action", "").upper()
        sign = 1 if action == "BUY" else -1
        shares = t.get("shares")
        price = t.get("price")
        if shares is None or price is None:
            continue
        rows.append(
            {
                "Date": t.get("timestamp", t.get("date")),
                "Security": t.get("ticker", "?"),
                "quantity": sign * float(shares),
                "price": float(price),
            }
        )
    return pd.DataFrame(rows) if rows else None


def _summarise_curve(curve: pd.Series) -> tuple[float, float, float, float]:
    """Return (total_return, cagr, sharpe, max_drawdown) as fractions."""
    if len(curve) < 2:
        return 0.0, 0.0, 0.0, 0.0
    start = float(curve.iloc[0])
    end = float(curve.iloc[-1])
    if start == 0:
        return 0.0, 0.0, 0.0, 0.0
    total_return = end / start - 1.0
    days = (curve.index[-1] - curve.index[0]).days
    cagr = (end / start) ** (365.0 / days) - 1.0 if days > 0 else 0.0
    daily_returns = curve.pct_change().dropna()
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = float(daily_returns.mean() / daily_returns.std() * (252**0.5))
    else:
        sharpe = 0.0
    cummax = curve.cummax()
    max_drawdown = float(((curve - cummax) / cummax).min())
    return float(total_return), float(cagr), sharpe, max_drawdown


def run_mirror_backtest(
    config: MirrorConfig,
    start: date,
    end: date,
    capital: float,
) -> BacktestResult:
    if not config.source.startswith("agent:"):
        raise MirrorError(
            f"Unsupported mirror source {config.source!r} — only agent:<id> is supported in v1"
        )
    agent_id = config.source[len("agent:") :]
    if agent_id not in AGENT_IDS:
        raise MirrorError(f"Unknown Midas agent {agent_id!r}")

    series = _load_agent_curve(agent_id)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    series = series[(series.index >= start_ts) & (series.index <= end_ts)]
    if series.empty:
        raise MirrorError(
            f"Agent {agent_id!r} has no portfolio data in window {start}..{end}"
        )

    series = (series / float(series.iloc[0])) * float(capital)
    total_return, cagr, sharpe, max_drawdown = _summarise_curve(series)

    transactions = _load_agent_transactions(agent_id)
    if transactions is not None and not transactions.empty:
        # Trade timestamps may carry timezone info; strip it to compare
        # against tz-naive start/end Timestamps.
        ts = pd.to_datetime(transactions["Date"], utc=True).dt.tz_localize(None)
        transactions = transactions[(ts >= start_ts) & (ts <= end_ts)]
        if transactions.empty:
            transactions = None

    return BacktestResult(
        strategy_id=f"mirror-{agent_id}",
        strategy_name=f"Mirror: {agent_id}",
        total_return=total_return,
        cagr=cagr,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        daily_values=series,
        transactions=transactions,
    )
