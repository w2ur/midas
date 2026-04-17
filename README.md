# Midas — AI Fund Manager

Personal AI fund manager that autonomously analyzes markets, makes investment decisions, and manages portfolios. Two execution engines work together: **bt** (Python backtesting framework) runs deterministic rule-based strategies, while **Claude Code agents** handle analytical strategies that require judgment.

## Architecture

Midas uses a composable strategy system where every strategy is defined by four independent axes:

```
Strategy = Universe × Selector × Manager × Funding + dividend mode
```

- **Universe**: what assets to consider (S&P 500, crypto top 20, congressional trades, etc.)
- **Selector**: when to buy (golden cross, RSI oversold, fear & greed, etc.)
- **Manager**: how to size and exit (equal weight, grid, trailing stop, etc.)
- **Funding**: how capital enters (lump sum, DCA monthly, etc.)

Deterministic strategies are backtested against years of historical data. Analytical strategies run daily as Claude agents with distinct personas and mandates.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run Backtests

```bash
# Single strategy
python scripts/run_backtest.py --strategy coin-flip-baseline --from 2024-01-01

# All strategies
python scripts/run_backtest.py --all --from 2024-01-01

# Factor research (all combinations)
python scripts/run_all_combos.py --universes etf-broad --from 2024-01-01
```

## Dashboard

```bash
streamlit run app/main.py
# Opens http://localhost:8501
```

## Add a Strategy

Create a JSON file in `data/strategies/`:

```json
{
  "id": "my-strategy",
  "name": "My Custom Strategy",
  "universe": "sp500",
  "selector": "golden-cross",
  "manager": "equal-weight",
  "funding": {"initial": 10000, "monthly_addition": 500},
  "dividends": "reinvest",
  "rules": {"maxPositions": 10, "maxPositionPct": 20, "minHoldDays": 3}
}
```

## Content Pipeline

Each daily session produces a complete output bundle combining all agent activity:

1. **Market data fetch** — benchmark values pulled once at session start.
2. **Claude trading agents** — 10 agents receive persona + market context + (eventually) their own journal. Output: `{commentary, trades}` per agent.
3. **Orders pipeline** — trades route through the Brain/Hands split (see below).
4. **Post generation** — each agent authors 1–3 short posts for the Midas Feed; prompts and parsing live in `engine/posts.py`.
5. **The Oracle narration** — the 11th agent (non-trader) produces a daily blog draft and 1–3 narrator posts via `engine/blog.py`.
6. **Bundle assembly** — `engine/output_bundle.py` assembles `data/output/YYYY-MM-DD.json` containing trades, fills, posts, blog, portfolios, leaderboard. Day number is retry-idempotent.

Local artifacts (gitignored) land in `data/posts/`, `data/blog/`, `data/output/`.

## Orders Pipeline

Trades never mutate portfolios directly. Instead:

1. Agent outputs `{action, ticker, shares, reasoning}`.
2. The orchestrator (`scripts/daily_session.py::step_author_orders`) appends a canonical `Order` record to `data/orders/outbox/YYYY-MM-DD.jsonl`.
3. The paper broker (`engine/paper_broker.py::fill_day`) applies 9 safety rails (notional cap, order-count cap, universe allowlist, drawdown halt, price lookup, cash/position checks, long-only shares>0, malformed-line resilience, apply_trade fault tolerance) and writes `data/orders/inbox/YYYY-MM-DD.jsonl`.
4. Filled orders mutate portfolios via `PortfolioManager.apply_trade`.

This split implements the **Brain / Hands** principle documented in CLAUDE.md. Real-money execution later is a drop-in broker swap.

Per-agent safety rails live in `data/agent_config/{agent_id}.json` (committed). Ticker → currency overrides live in `data/ticker_currencies.json` (committed).

---

Made with care by [William](https://william.revah.paris)
