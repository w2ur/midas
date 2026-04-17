"""Orchestrator for the daily Midas trading session.

Two modes:

1. **Snapshot-only CLI** (`run_daily_session()`): fetch market data, snapshot
   portfolios, commit & push. Intended for manual EOD runs and CI health
   checks — does NOT dispatch Claude agents. Every step is idempotent.

2. **Full Ring 1 + Ring 2 pipeline** (step_* helpers, called from an
   orchestrating Claude Code session that parallelises agent dispatch):
     - step_author_orders()                → data/orders/outbox/
     - step_fill_orders()                  → data/orders/inbox/ + portfolio mutation
     - step_build_post_prompts()           → prompts for the orchestrator
     - step_load_memories()                → dict[agent_id, str]
     - step_build_oracle_prompt()          → Oracle prompt (optionally with journals)
     - step_save_content()                 → data/posts/, data/blog/, data/output/
     - step_build_memory_update_prompts()  → Ring 2 session-end rewrite prompts
     - step_save_memories()                → data/agent_memory/

Usage (snapshot-only):
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

from engine.agent_memory import (
    build_memory_update_prompt,
    load_journal,
    save_journal,
)
from engine.blog import build_oracle_prompt, save_daily_blog_draft
from engine.market_data import MarketDataFetcher
from engine.orders import Order, append_order, make_order_id
from engine.output_bundle import (
    assemble_output_bundle,
    get_day_number,
    save_output_bundle,
)
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


# ---------------------------------------------------------------------------
# Ring 1 content pipeline — step functions for the orchestrator to call
# after Claude agents have produced their {commentary, trades} output.
# Not wired into run_daily_session(); call them from the orchestrating session.
# ---------------------------------------------------------------------------


def step_author_orders(
    agent_id: str, trades: list[dict], trade_date: date, currency: str
) -> int:
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
    agent_memories: dict[str, str] | None = None,
) -> str:
    """Step 5b — build The Oracle's daily narration prompt.

    Does NOT call Claude. Returns the prompt string the orchestrator dispatches
    to the-oracle agent. When `agent_memories` is provided, each agent's latest
    journal is digested into the prompt so The Oracle can quote specific entries.
    """
    print("\n=== Step 5b: Build Oracle prompt ===")
    day_number = get_day_number()
    prompt = build_oracle_prompt(
        day_number=day_number,
        market_data=market_data,
        agent_results=agent_results,
        agent_posts=agent_posts,
        leaderboard=leaderboard,
        agent_memories=agent_memories,
    )
    print(f"  Built Oracle prompt (day {day_number})")
    return prompt


def step_load_memories(agent_ids: list[str]) -> dict[str, str]:
    """Step 5c — load each agent's journal from disk for Oracle prompt assembly.

    Returns a dict keyed by agent_id. Missing journals become empty strings so
    the Oracle prompt can still render a "first session" marker.
    """
    print("\n=== Step 5c: Load agent memories ===")
    memories = {aid: load_journal(aid) for aid in agent_ids}
    non_empty = sum(1 for v in memories.values() if v.strip())
    print(f"  Loaded {non_empty}/{len(agent_ids)} non-empty journals")
    return memories


def step_build_memory_update_prompts(
    agent_results: dict[str, dict],
    agent_posts: dict[str, list[dict]],
    portfolio_summaries: dict[str, dict],
    day_number: int,
) -> dict[str, str]:
    """Step 7a — build session-end journal-rewrite prompts for every agent.

    Does NOT call Claude. Returns {agent_id: prompt} the orchestrator dispatches.
    Covers all 11 agents (the 10 traders plus the-oracle). Each agent reads its
    current journal from disk in-prompt; we embed it here so the dispatched
    prompt is fully self-contained.
    """
    print("\n=== Step 7a: Build memory-update prompts ===")
    prompts: dict[str, str] = {}
    # Traders
    for agent_id, result in agent_results.items():
        prompts[agent_id] = build_memory_update_prompt(
            agent_id=agent_id,
            day_number=day_number,
            current_journal=load_journal(agent_id),
            trades_today=result.get("trades", []),
            posts_today=agent_posts.get(agent_id, []),
            portfolio_summary=portfolio_summaries.get(agent_id, {}),
        )
    # The Oracle doesn't trade; its journal update prompt has no trades.
    prompts["the-oracle"] = build_memory_update_prompt(
        agent_id="the-oracle",
        day_number=day_number,
        current_journal=load_journal("the-oracle"),
        trades_today=[],
        posts_today=agent_posts.get("the-oracle", []),
        portfolio_summary={"currency": "EUR"},
    )
    print(f"  Built {len(prompts)} memory-update prompts")
    return prompts


def step_save_memories(new_journals: dict[str, str]) -> int:
    """Step 7b — persist rewritten journals back to data/agent_memory/.

    Parameters
    ----------
    new_journals:
        {agent_id: new_journal_content} returned by orchestrator after dispatch.
        Empty/blank values are skipped so a partial round doesn't wipe a journal.

    Returns the number of journals actually written.
    """
    print("\n=== Step 7b: Save updated memories ===")
    written = 0
    for agent_id, content in new_journals.items():
        if not content or not content.strip():
            print(f"  [SKIP] {agent_id}: empty response")
            continue
        save_journal(agent_id, content)
        written += 1
    print(f"  Saved {written}/{len(new_journals)} journals")
    return written


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
                prices_df = fetcher.fetch_prices(
                    tickers, start=start, end=snapshot_date
                )
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

        print(
            f"  Snapshotted {strategy_id}: value={portfolio_value:.2f}, cash={portfolio.cash:.2f}"
        )
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
        subprocess.run(
            ["git", "commit", "-m", commit_msg], cwd=_PROJECT_ROOT, check=True
        )
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
    """Snapshot-only EOD run.

    Dispatches no Claude agents — the full trading round is driven by an
    orchestrating Claude Code session that calls the step_* helpers directly.
    Use this CLI for manual snapshot refreshes or CI health checks.
    """
    print(f"Midas daily snapshot — {date.today().isoformat()}")
    print("=" * 50)

    market_payload = step_fetch_market_data()
    step_update_snapshots(market_payload)
    step_git_commit_push(dry_run=dry_run)

    print("\n=== Snapshot complete ===")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Midas daily trading session orchestrator."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run all steps but skip the git commit and push.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_daily_session(dry_run=args.dry_run)
