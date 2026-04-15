"""Orchestrator for the daily Midas trading session.

Steps:
  1. Fetch today's market data  → data/market/today.json
  2. Deterministic strategies   → placeholder (use backtest CLI)
  3. Claude agents              → placeholder (dispatched by orchestrating session)
  4. Update daily snapshots     → data/portfolios/{id}/snapshots.json
  5. Git commit and push data changes

Usage:
    python scripts/daily_session.py
    python scripts/daily_session.py --dry-run   # skip git commit/push
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

# Add project root to sys.path so engine imports work when run directly.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from engine.market_data import MarketDataFetcher
from engine.portfolio import PortfolioManager
from scripts.fetch_market_data import fetch_and_save as fetch_market_data


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------


def step_fetch_market_data() -> dict:
    """Step 1 — Fetch today's benchmark values."""
    print("\n=== Step 1: Fetch market data ===")
    return fetch_market_data()


def step_deterministic_strategies() -> None:
    """Step 2 — Deterministic strategies (bt-based).

    Not yet implemented in this script.
    Use the backtest CLI:
        python scripts/run_backtest.py --all
    Live execution via bt is planned for a future session.
    """
    print("\n=== Step 2: Deterministic strategies ===")
    print("  [NOT YET IMPLEMENTED] — Use the backtest CLI:")
    print("    python scripts/run_backtest.py --all")


def step_claude_agents() -> None:
    """Step 3 — Claude analytical agents.

    Not dispatched here. Claude agents should be dispatched by the
    orchestrating Claude Code session that invokes this script, so they
    can be parallelised and observed interactively.
    """
    print("\n=== Step 3: Claude agents ===")
    print("  [DELEGATED] — Should be dispatched by the orchestrating session.")
    print("  Agents: trend-trader, contrarian, macro-quant, dividend-collector,")
    print("          momentum-surfer, risk-manager")


def step_update_snapshots(market_payload: dict) -> list[str]:
    """Step 4 — Append daily snapshots for all active portfolios.

    A portfolio is "active" if it has a portfolio.json on disk.

    Parameters
    ----------
    market_payload:
        The dict returned by fetch_market_data, containing "date" and
        "benchmarks".

    Returns
    -------
    list[str]
        Strategy IDs that were snapshotted.
    """
    print("\n=== Step 4: Update daily snapshots ===")

    portfolios_dir = _PROJECT_ROOT / "data" / "portfolios"
    if not portfolios_dir.exists():
        print("  No portfolios directory found — skipping.")
        return []

    manager = PortfolioManager(base_dir=portfolios_dir)
    snapshot_date = date.fromisoformat(market_payload["date"])
    benchmarks = market_payload["benchmarks"]

    # Fetch current prices to compute positions_value for each portfolio.
    cache_dir = _PROJECT_ROOT / "data" / "cache"
    fetcher = MarketDataFetcher(cache_dir=cache_dir)

    snapshotted: list[str] = []

    for portfolio_dir in sorted(portfolios_dir.iterdir()):
        if not portfolio_dir.is_dir():
            continue
        portfolio_json = portfolio_dir / "portfolio.json"
        if not portfolio_json.exists():
            continue

        strategy_id = portfolio_dir.name

        try:
            portfolio = manager.load(strategy_id)
        except Exception as exc:
            print(f"  [WARN] Could not load {strategy_id}: {exc}")
            continue

        # Compute positions value from current prices.
        positions_value = 0.0
        if portfolio.positions:
            tickers = [p.ticker for p in portfolio.positions]
            from datetime import timedelta
            start = snapshot_date - timedelta(days=7)
            try:
                prices_df = fetcher.fetch_prices(tickers, start=start, end=snapshot_date)
                if not prices_df.empty:
                    latest_prices = prices_df.iloc[-1].to_dict()
                    positions_value = sum(
                        p.shares * latest_prices.get(p.ticker, p.avg_cost)
                        for p in portfolio.positions
                    )
                else:
                    # Fall back to cost basis.
                    positions_value = portfolio.cost_basis
            except Exception:
                positions_value = portfolio.cost_basis

        portfolio_value = portfolio.cash + positions_value

        manager.add_snapshot(
            strategy_id=strategy_id,
            snapshot_date=snapshot_date,
            portfolio_value=portfolio_value,
            cash=portfolio.cash,
            positions_value=positions_value,
            benchmarks=benchmarks,
        )

        print(f"  Snapshotted {strategy_id}: value={portfolio_value:.2f}, cash={portfolio.cash:.2f}")
        snapshotted.append(strategy_id)

    if not snapshotted:
        print("  No active portfolios found.")

    return snapshotted


def step_git_commit_push(dry_run: bool = False) -> None:
    """Step 5 — Git commit and push data changes."""
    print("\n=== Step 5: Git commit and push ===")

    if dry_run:
        print("  [DRY RUN] Skipping git operations.")
        return

    data_dir = str(_PROJECT_ROOT / "data")

    try:
        # Stage data/ changes.
        subprocess.run(["git", "add", data_dir], cwd=_PROJECT_ROOT, check=True)

        # Check if there is anything to commit.
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=_PROJECT_ROOT,
        )
        if result.returncode == 0:
            print("  Nothing to commit — data unchanged.")
            return

        today_str = date.today().isoformat()
        commit_msg = f"chore: daily snapshot {today_str}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=_PROJECT_ROOT, check=True)
        print(f"  Committed: {commit_msg}")

        subprocess.run(["git", "push"], cwd=_PROJECT_ROOT, check=True)
        print("  Pushed to remote.")

    except subprocess.CalledProcessError as exc:
        print(f"  [ERROR] Git operation failed: {exc}")
        raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_daily_session(dry_run: bool = False) -> None:
    """Run all steps of the daily trading session."""
    print(f"Midas daily session — {date.today().isoformat()}")
    print("=" * 50)

    market_payload = step_fetch_market_data()
    step_deterministic_strategies()
    step_claude_agents()
    step_update_snapshots(market_payload)
    step_git_commit_push(dry_run=dry_run)

    print("\n=== Daily session complete ===")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Midas daily trading session orchestrator.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run all steps but skip the git commit and push.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_daily_session(dry_run=args.dry_run)
