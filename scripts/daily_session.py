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
     - step_build_leaderboard()            → ranked rows (EUR mtm / €10k inception)
     - step_build_oracle_prompt()          → Oracle prompt (optionally with journals)
     - build_portfolio_summaries()         → dict for ALL 10 agents (carry-forward)
     - step_save_content()                 → data/posts/, data/blog/, data/output/
     - step_build_memory_update_prompts()  → Ring 2 session-end rewrite prompts
     - step_save_memories()                → data/agent_memory/
     - step_build_baselines()              → data/baselines/ (idempotent recompute)

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
from engine.ohlcv_store import latest_close_on_or_before
from engine.orders import Order, append_order, make_order_id
from engine.types import Portfolio
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


def step_build_post_prompts(
    agent_results: dict[str, dict],
    oracle_blog: str | None = None,
) -> dict[str, str]:
    """Step 5a — build post-generation prompts for each trading agent.

    Does NOT call Claude. Returns a dict of {agent_id: prompt_str} the orchestrator
    dispatches to each agent. The Oracle is excluded — it gets a different prompt
    via step_build_oracle_prompt.

    When the post round runs AFTER the Oracle (current pipeline ordering),
    pass `oracle_blog=blog_draft.body_md` so each agent can react to the
    Oracle's framing as well as to other agents' raw moves.
    """
    print("\n=== Step 5a: Build post prompts ===")
    prompts: dict[str, str] = {}
    for agent_id in agent_results:
        if agent_id in AGENT_POST_TIMES:  # trading agents only
            prompts[agent_id] = build_post_prompt(
                agent_id, agent_results, oracle_blog=oracle_blog
            )
    print(f"  Built {len(prompts)} post prompts")
    return prompts


def step_build_leaderboard(
    portfolio_summaries: dict[str, dict],
    on: date | None = None,
) -> list[dict]:
    """Step 5a-bis — canonical leaderboard for the day.

    Returns ``[{"rank": int, "agent": str, "return_pct": float}]`` sorted by
    EUR-equivalent MTM descending, anchored to the €10,000 inception baseline
    that ``engine/daily_log.py`` and ``engine/baselines.py`` use everywhere
    else.

    Always call this helper instead of hand-rolling the calculation from
    ``snapshots.json``. The first persisted snapshot is NOT inception for
    every agent — agents whose seed portfolio contained non-cash positions
    (e.g. Monsieur Forex starts with FX cash legs, World starts with
    multi-currency baskets) have ``snapshots[0]['portfolio_value']`` already
    reflecting non-zero P&L. Using that as the baseline materially
    understates their returns. The 2026-05-15 weekday session shipped that
    bug; this helper is the fix.
    """
    from engine.valuation import portfolio_mtm_eur

    print("\n=== Step 5a-bis: Build leaderboard ===")
    rows: list[dict] = []
    for agent_id, summary in portfolio_summaries.items():
        eur_mtm = portfolio_mtm_eur(summary, on)
        if eur_mtm is None:
            continue
        rows.append({
            "agent": agent_id,
            "return_pct": (eur_mtm / 10_000 - 1) * 100,
        })
    rows.sort(key=lambda r: r["return_pct"], reverse=True)
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    print(f"  Ranked {len(rows)} agents (top: {rows[0]['agent']} {rows[0]['return_pct']:+.2f}%)")
    return rows


def step_build_oracle_prompt(
    market_data: dict,
    agent_results: dict[str, dict],
    agent_posts: dict[str, list[dict]] | None = None,
    leaderboard: list[dict] | None = None,
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


def build_portfolio_summaries() -> dict[str, dict]:
    """Build the canonical per-agent portfolio summary dict for ALL 10 trading
    agents. Use this output as the `portfolio_summaries` argument to
    `step_save_content` so the bundle's agents map carries forward last-known
    portfolio state for non-running agents (weekend cadence, etc.).

    Reads `data/portfolios/{agent_id}/portfolio.json` via PortfolioManager.
    Agents with no portfolio.json on disk are skipped (defensive — should not
    happen in production after Day 1).

    Summary shape: {cash, deployed, positions, currency}
    where `positions` is the Portfolio.to_dict() position list and
    `deployed` is `portfolio.cost_basis`.
    """
    from engine.posts import AGENT_POST_TIMES

    portfolios_dir = _PROJECT_ROOT / "data" / "portfolios"
    manager = PortfolioManager(base_dir=portfolios_dir)

    summaries: dict[str, dict] = {}
    for agent_id in AGENT_POST_TIMES.keys():
        if not (portfolios_dir / agent_id / "portfolio.json").exists():
            continue
        portfolio = manager.load(agent_id)
        d = portfolio.to_dict()
        summaries[agent_id] = {
            "cash": d["cash"],
            "deployed": portfolio.cost_basis,
            "positions": d["positions"],
            "currency": d["currency"],
        }
    return summaries


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


def _compute_positions_value(
    portfolio: Portfolio, on: date, store: Path | None = None
) -> float:
    """Mark a portfolio's open positions to market using per-ticker latest closes.

    For each position, walks the OHLCV store for the most recent close at or
    before `on`. Falls back to avg_cost when no row exists. This carries
    European tickers forward when their same-day close hasn't landed yet —
    avoids the NaN portfolio_value bug where pandas left-joined a pricing
    DataFrame whose `iloc[-1]` row contained NaN for lagging markets.
    """
    total = 0.0
    for p in portfolio.positions:
        price = latest_close_on_or_before(p.ticker, on, store=store)
        if price is None:
            price = p.avg_cost
        total += p.shares * price
    return total


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

        positions_value = _compute_positions_value(portfolio, snapshot_date)
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


def step_build_baselines() -> None:
    """Step 9a — Baselines.

    Recomputes data/baselines/ for Day 1 → today, full-rewrite and idempotent.
    Runs AFTER portfolio mutations so the benchmark window matches the
    freshly-appended agent snapshots. Uses backfill_baselines constants as
    the single source of truth for universes + max_positions.
    """
    print("\n=== Step 9a: Build baselines ===")
    from datetime import date as _date

    from engine.baselines import DAY_ONE, build_all_baselines
    from scripts.backfill_baselines import AGENT_MAX_POSITIONS, AGENT_UNIVERSES

    build_all_baselines(
        universes_by_agent=AGENT_UNIVERSES,
        from_date=DAY_ONE,
        to_date=_date.today(),
        max_positions_by_agent=AGENT_MAX_POSITIONS,
    )


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

        # Commit any staged data changes the orchestrator hasn't already
        # committed. (Orchestrators that commit themselves with a richer
        # message — "chore: weekday session …" — will land here with nothing
        # left staged, which is fine.)
        diff_result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=_PROJECT_ROOT,
        )
        if diff_result.returncode != 0:
            today_str = date.today().isoformat()
            commit_msg = f"chore: daily snapshot {today_str}"
            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=_PROJECT_ROOT,
                check=True,
            )
            print(f"  Committed: {commit_msg}")

        # Always push HEAD to origin/main. Cloud sandbox sessions
        # (RemoteTrigger) check out a throwaway branch like `claude/<slug>`;
        # without an explicit refspec, `git push` would publish that branch
        # instead of advancing main, leaving the daily snapshot off the
        # public deploy. Fast-forward only — anything else is a real
        # conflict that should fail loudly.
        ahead = subprocess.run(
            ["git", "rev-list", "--count", "origin/main..HEAD"],
            cwd=_PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        if int(ahead.stdout.strip() or "0") == 0:
            print("  Nothing to push — HEAD is at origin/main.")
            return

        # Primary path: push directly to origin/main. Fallback path
        # (added 2026-05-08 after the harness started 403'ing main pushes
        # from cloud sandboxes): push the sandbox branch instead, and let
        # .github/workflows/auto-merge-session.yml take the merge to main.
        push_main = subprocess.run(
            ["git", "push", "origin", "HEAD:main"],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if push_main.returncode == 0:
            print("  Pushed to origin/main.")
            return

        stderr = (push_main.stderr or "").strip()
        stdout = (push_main.stdout or "").strip()
        print(f"  [WARN] Push to origin/main failed: {stderr or stdout}")
        print(
            "  Falling back to push current branch — auto-merge-session.yml will take it to main."
        )

        subprocess.run(
            ["git", "push", "origin", "HEAD"],
            cwd=_PROJECT_ROOT,
            check=True,
        )
        branch_name = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=_PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        print(
            f"  Pushed to sandbox branch '{branch_name}'. Watch for auto-merge-session workflow on GitHub."
        )

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
    step_build_baselines()
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
