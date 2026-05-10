"""Backfill historical NaN snapshots and dedupe by date.

Why this exists
---------------
Until 2026-05-10, `step_update_snapshots` priced positions by
`prices_df.iloc[-1].to_dict()` from a left-joined DataFrame. When a held
ticker had no row for the DataFrame's last date (e.g. European tickers
lagging US closes by one day), the dict held NaN for that key and
`portfolio_value` came out NaN. World had 17/20 NaN snapshots,
yolo-sapiens-eur 12/20, plus a handful of duplicate-date appendages from
session retries.

What it does
------------
For each portfolio under `data/portfolios/`:
  1. Reads `snapshots.json` and `trades.json`.
  2. For each unique date, picks the existing entry to keep — preferring
     a non-NaN `portfolio_value` if any, else the last-written one.
  3. If the kept entry has a NaN `portfolio_value`: replays trades up to
     that date to reconstruct positions, prices each via
     `latest_close_on_or_before`, and rewrites `positions_value` and
     `portfolio_value`. Cash and benchmarks come from the existing
     entry — unchanged.
  4. Sorts by date, writes back.

Idempotent. Safe to re-run. Does not touch entries whose
`portfolio_value` is already a valid float.

Usage
-----
    python scripts/backfill_snapshots.py            # all portfolios
    python scripts/backfill_snapshots.py --dry-run  # report only
    python scripts/backfill_snapshots.py world      # single portfolio
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from engine.ohlcv_store import latest_close_on_or_before


_PORTFOLIOS_DIR = _PROJECT_ROOT / "data" / "portfolios"


def _is_nan(value: float | None) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _trade_date(trade: dict) -> date:
    ts = trade["timestamp"]
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts).date()


def _positions_on_date(trades: list[dict], on_date: date) -> dict[str, float]:
    """Replay trades up to (and including) `on_date` and return ticker → shares."""
    positions: dict[str, float] = {}
    for t in trades:
        if _trade_date(t) > on_date:
            continue
        delta = t["shares"] if t["action"] == "BUY" else -t["shares"]
        positions[t["ticker"]] = positions.get(t["ticker"], 0.0) + delta
    return {tk: sh for tk, sh in positions.items() if sh > 1e-9}


def _last_trade_price_at_or_before(
    trades: list[dict], ticker: str, on_date: date
) -> float | None:
    """Walk trades for the last fill price for `ticker` ≤ on_date — used as a
    fallback when the OHLCV store has no row for the ticker on/before that date."""
    last_price: float | None = None
    for t in trades:
        if t["ticker"] != ticker:
            continue
        if _trade_date(t) > on_date:
            continue
        last_price = float(t["price"])
    return last_price


def _value_positions(
    positions: dict[str, float], on_date: date, trades: list[dict]
) -> float:
    total = 0.0
    for ticker, shares in positions.items():
        price = latest_close_on_or_before(ticker, on_date)
        if price is None:
            price = _last_trade_price_at_or_before(trades, ticker, on_date)
        if price is None:
            # No information at all — skip this position (treat as zero).
            continue
        total += shares * price
    return total


def _pick_per_date(snapshots: list[dict]) -> dict[str, dict]:
    """For each date in `snapshots`, pick the entry to keep.

    Prefers a non-NaN `portfolio_value` when available; otherwise keeps
    the last-written entry for that date.
    """
    keep: dict[str, dict] = {}
    for s in snapshots:
        d = s.get("date")
        if not d:
            continue
        existing = keep.get(d)
        if existing is None:
            keep[d] = s
            continue
        existing_nan = _is_nan(existing.get("portfolio_value"))
        new_nan = _is_nan(s.get("portfolio_value"))
        if existing_nan and not new_nan:
            keep[d] = s
        elif existing_nan == new_nan:
            keep[d] = s  # later wins among same-validity entries
    return keep


def backfill_portfolio(strategy_id: str, dry_run: bool = False) -> tuple[int, int, int]:
    """Backfill one portfolio. Returns (kept, deduped, repaired)."""
    portfolio_dir = _PORTFOLIOS_DIR / strategy_id
    snapshots_path = portfolio_dir / "snapshots.json"
    trades_path = portfolio_dir / "trades.json"

    if not snapshots_path.exists():
        print(f"  [{strategy_id}] no snapshots.json — skipping")
        return 0, 0, 0

    snapshots: list[dict] = json.loads(snapshots_path.read_text())
    trades: list[dict] = (
        json.loads(trades_path.read_text()) if trades_path.exists() else []
    )

    original = len(snapshots)
    picked = _pick_per_date(snapshots)
    deduped = original - len(picked)

    repaired = 0
    rewritten: list[dict] = []
    for d in sorted(picked):
        entry = dict(picked[d])  # shallow copy
        if _is_nan(entry.get("portfolio_value")):
            on = date.fromisoformat(d)
            positions = _positions_on_date(trades, on)
            positions_value = _value_positions(positions, on, trades)
            cash = float(entry.get("cash", 0.0))
            entry["positions_value"] = positions_value
            entry["portfolio_value"] = cash + positions_value
            repaired += 1
        rewritten.append(entry)

    print(
        f"  [{strategy_id}] entries={original} → {len(rewritten)} "
        f"(deduped {deduped}, repaired {repaired})"
    )

    if not dry_run and (deduped or repaired):
        snapshots_path.write_text(json.dumps(rewritten, indent=2) + "\n")

    return len(rewritten), deduped, repaired


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "strategy_id",
        nargs="?",
        default=None,
        help="Single portfolio to backfill (default: all).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not _PORTFOLIOS_DIR.exists():
        print("No data/portfolios/ directory.")
        return 1

    if args.strategy_id:
        targets = [args.strategy_id]
    else:
        targets = sorted(d.name for d in _PORTFOLIOS_DIR.iterdir() if d.is_dir())

    total_deduped = 0
    total_repaired = 0
    for sid in targets:
        _, deduped, repaired = backfill_portfolio(sid, dry_run=args.dry_run)
        total_deduped += deduped
        total_repaired += repaired

    suffix = " (dry-run)" if args.dry_run else ""
    print(
        f"\nDone{suffix}. Deduped {total_deduped} duplicate dates, "
        f"repaired {total_repaired} NaN entries."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
