"""Orchestrator for the daily Midas trading session.

Core pipeline (run by run_daily_session):
  1. Fetch today's market data       → data/market/today.json
  2. Deterministic strategies         → placeholder (use backtest CLI)
  3. Claude trading agents            → placeholder (dispatched by orchestrating session)
  4. Update daily snapshots           → data/portfolios/{id}/snapshots.json
  5. Git commit and push data changes

Ring 1 content pipeline (called by the orchestrating Claude Code session after
agent {commentary, trades} output is collected):
  3a. step_author_orders()            → data/orders/outbox/YYYY-MM-DD.jsonl
  3b. step_fill_orders()              → data/orders/inbox/YYYY-MM-DD.jsonl + portfolio mutation
  5a. step_build_post_prompts()       → returns prompts for orchestrator to dispatch
  5b. step_build_oracle_prompt()      → returns prompt for orchestrator to dispatch
  6.  step_save_content()             → data/posts/, data/blog/, data/output/

Usage:
    python scripts/daily_session.py
    python scripts/daily_session.py --dry-run   # skip git commit/push
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# Add project root to sys.path so engine imports work when run directly.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from engine.blog import build_oracle_prompt, save_daily_blog_draft
from engine.market_data import MarketDataFetcher
from engine.orders import Order, append_order, make_order_id
from engine.output_bundle import assemble_output_bundle, get_day_number, save_output_bundle
from engine.paper_broker import fill_day
from engine.portfolio import PortfolioManager
from engine.posts import (
    AGENT_DISPLAY_NAMES,
    AGENT_POST_TIMES,
    PostPayload,
    build_post_prompt,
    save_daily_posts,
)
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


# ---------------------------------------------------------------------------
# Ring 1 content pipeline — step functions for the orchestrator to call
# after Claude agents have produced their {commentary, trades} output.
# Not wired into run_daily_session(); call them from the orchestrating session.
# ---------------------------------------------------------------------------


def step_author_orders(agent_id: str, trades: list[dict], trade_date: date, currency: str) -> int:
    """Step 3a — convert an agent's trades[] into outbox orders.

    Parameters
    ----------
    agent_id:
        Agent authoring the orders (e.g. "satoshi").
    trades:
        List of trade dicts with keys: action ("BUY"|"SELL"), ticker, shares, reasoning.
    trade_date:
        The session's trading date.
    currency:
        Agent's portfolio base currency (e.g. "EUR"). Matches portfolio.currency.

    Returns
    -------
    Number of orders appended to the outbox (malformed trades raise at Order construction
    time; callers are expected to pass well-formed trades from agent JSON).
    """
    print(f"\n=== Step 3a: Author orders for {agent_id} ({len(trades)} trades) ===")
    for seq, t in enumerate(trades, start=1):
        order = Order(
            order_id=make_order_id(trade_date, agent_id, seq),
            ts=datetime.now(timezone.utc),
            agent_id=agent_id,
            action=t["action"],
            ticker=t["ticker"],
            shares=float(t["shares"]),
            reasoning=t.get("reasoning", ""),
            currency=currency,
        )
        append_order(trade_date, order)
    return len(trades)


def step_fill_orders(trade_date: date, portfolio_manager: PortfolioManager) -> list:
    """Step 3b — invoke the paper broker on the day's outbox.

    The broker reads data/orders/outbox/YYYY-MM-DD.jsonl, applies safety rails,
    writes data/orders/inbox/YYYY-MM-DD.jsonl, and (for successful fills)
    mutates portfolios via PortfolioManager.apply_trade.
    """
    print("\n=== Step 3b: Fill orders (paper broker) ===")
    fills = fill_day(trade_date, portfolio_manager)
    filled = sum(1 for f in fills if f.status == "filled")
    rejected = sum(1 for f in fills if f.status == "rejected")
    print(f"  {filled} filled, {rejected} rejected out of {len(fills)}")
    return fills


def step_build_post_prompts(agent_results: dict[str, dict]) -> dict[str, str]:
    """Step 5a — build post-generation prompts for each trading agent.

    Does NOT call Claude. Returns a dict of {agent_id: prompt_str} the orchestrator
    dispatches to each agent. The Oracle is excluded — it gets a different prompt
    via step_build_oracle_prompt.
    """
    print("\n=== Step 5a: Build post prompts ===")
    prompts: dict[str, str] = {}
    for agent_id in agent_results:
        if agent_id in AGENT_POST_TIMES:  # trading agents only
            prompts[agent_id] = build_post_prompt(agent_id, agent_results)
    print(f"  Built {len(prompts)} post prompts")
    return prompts


def step_build_oracle_prompt(
    market_data: dict,
    agent_results: dict[str, dict],
    agent_posts: dict[str, list[dict]],
    leaderboard: list[dict],
) -> str:
    """Step 5b — build The Oracle's daily narration prompt.

    Does NOT call Claude. Returns the prompt string the orchestrator dispatches
    to the-oracle agent.
    """
    print("\n=== Step 5b: Build Oracle prompt ===")
    day_number = get_day_number()
    prompt = build_oracle_prompt(
        day_number=day_number,
        market_data=market_data,
        agent_results=agent_results,
        agent_posts=agent_posts,
        leaderboard=leaderboard,
    )
    print(f"  Built Oracle prompt (day {day_number})")
    return prompt


def step_save_content(
    bundle_date: date,
    market_data: dict,
    agent_results: dict[str, dict],
    agent_posts: dict[str, list[PostPayload]],
    portfolio_summaries: dict[str, dict],
    leaderboard: list[dict],
    blog_draft,
    oracle_posts: list[PostPayload],
) -> dict:
    """Step 6 — persist posts, blog draft, and output bundle.

    Returns the assembled bundle dict so callers can log its shape or pass it
    onwards (e.g. to a future publisher).
    """
    print("\n=== Step 6: Save content ===")
    posts_path = save_daily_posts(bundle_date, agent_posts)
    print(f"  Saved posts → {posts_path.name}")
    blog_path = save_daily_blog_draft(bundle_date, blog_draft)
    print(f"  Saved blog draft → {blog_path.name}")
    bundle = assemble_output_bundle(
        bundle_date=bundle_date,
        market_data=market_data,
        agent_results=agent_results,
        agent_posts=agent_posts,
        portfolio_summaries=portfolio_summaries,
        leaderboard=leaderboard,
        blog_draft=blog_draft,
        oracle_posts=oracle_posts,
    )
    bundle_path = save_output_bundle(bundle_date, bundle)
    print(f"  Saved output bundle → {bundle_path.name}")
    return bundle


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
