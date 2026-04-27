# Midas — AI Fund Manager

## Project Overview
Personal AI fund manager that autonomously analyzes markets, makes investment decisions, and manages portfolios. Two execution engines: bt (Python) for deterministic strategies, Claude agents for analytical ones. Local-only — runs on localhost via Streamlit.

## Tech Stack
- Python 3.12+, bt (backtesting), yfinance (market data), pandas-ta (indicators)
- Streamlit + Plotly (dashboard), pytest (testing)
- Claude Code agents for analytical trading strategies

## User-Facing Language
English

## Development
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Testing
```bash
pytest tests/ -v
```

## Dashboard
```bash
streamlit run app/main.py
```

## Project Structure
- `engine/` — Core trading logic: types, market data, bt adapter, backtest runner
- `engine/orders.py` — Order/Fill types + outbox/inbox JSONL serde (Brain/Hands primitive)
- `engine/paper_broker.py` — Hands side: 9 safety rails + fill logic + portfolio update
- `engine/posts.py` — post types, 11-agent display names + schedule (single source of truth)
- `engine/blog.py` — Oracle prompt builder + response parser + blog draft saver
- `engine/agent_memory.py` — Ring 2 per-agent journal I/O + digest/excerpt helpers + session-end rewrite prompt
- `engine/selectors/` — bt Algos for entry signals (golden cross, RSI, etc.)
- `engine/managers/` — bt Algos for position management (grid, trailing stop, etc.)
- `engine/universes/` — Universe resolvers (S&P 500, congressional, crypto, etc.)
- `scripts/` — CLI entry points for backtesting and daily sessions
- `app/` — Streamlit dashboard pages
- `data/strategies/` — Strategy spec JSON files
- `data/portfolios/` — Runtime portfolio state (gitignored)
- `data/agent_config/` — per-agent safety rails (committed)
- `data/ticker_currencies.json` — ticker → ISO currency override map (committed)
- `data/orders/{outbox,inbox}/` — Brain/Hands trade flow (committed)
- `data/agent_memory/` — Ring 2 per-agent journals, 11 markdown files, first-person + biased, rewritten each session (committed)
- `data/baselines/` — per-agent passive benchmark + coin-flip phantom portfolios, plus `global/msci_world.json`; same snapshot shape as `data/portfolios/`; written by `scripts/backfill_baselines.py` (one-shot) and refreshed by Step 9a of the daily session (committed)
- `.claude/agents/` — Ten trading agent personas (EUR/USD twins + Satoshi, Monsieur Forex, Goldfinger, World)
- `.claude/agents/the-oracle.md` — The Oracle narrator agent (does not trade; blog drafts + scoreboard posts)
- `engine/output_bundle.py` — assembles data/output/YYYY-MM-DD.json (single source of truth for API + retries)
- `data/posts/, data/blog/, data/output/` — daily artifacts (committed; see `.gitignore` comment)
- `site/` — Astro static site (Ring 3a) deployed to `midas.revah.paris` via Vercel; reads `data/` and `.claude/agents/` at build time
- `backtester/` — FastAPI service deployed to Google Cloud Run; wraps `engine.backtest.run_backtest` for the public `/simulate` page. Local dev: `uvicorn backtester.app:app --reload --port 8080`. Deploy: `backtester/README.md`. Consumed by the site via the `PUBLIC_BACKTESTER_URL` env var.

## Architecture Principle — Brain / Hands

All external-world integrations in Midas follow a **Brain / Hands split**:

- **Brain** — the Claude Code sandbox (daily trigger cron). Reads from disk, authors decisions (trades, posts, journal entries), writes to an outbox on disk. Holds no external credentials.
- **Hands** — separate workers (paper broker for simulation, future real-broker worker for live execution). Read outbox, validate against safety rails, execute, write confirmations to inbox on disk. Pure executors.

First application (Ring 1): trade execution.
- Agents write orders to `data/orders/outbox/YYYY-MM-DD.jsonl`.
- `engine/paper_broker.py` applies 9 safety rails, fills at end-of-day close from the OHLCV store, writes to `data/orders/inbox/YYYY-MM-DD.jsonl`.
- Fills with `status="filled"` mutate portfolios via `PortfolioManager.apply_trade`; rejections carry a reason code.

**Safety rails live in the Hands, not agent prompts.** The agent persona is aspirational; the broker is enforcing.

Real-money transition is a broker swap: replace `paper_broker.py` with an `ibie_broker.py` that talks to Interactive Brokers — same outbox/inbox contract, credentials held outside the sandbox. See `~/.claude/plans/2026-04-17-midas-public-experiment-design-v2.md` for the full experiment design.

## Real-Money Tax & Regulatory Context
- Operator is a **French tax resident**. All broker choices must serve France and expose a trading API.
- Approved brokers: **Interactive Brokers Ireland (IBIE)** for equities/ETFs/forex; **Kraken** (PSAN-registered in France) for crypto; **OANDA Europe (Ireland)** for dedicated forex.
- Never assume Alpaca, Robinhood, Schwab, Fidelity, or any US-residents-only broker — they're closed to this operator.
- PRIIPs KID requirement blocks many US-domiciled leveraged/inverse ETFs for EU retail. Verify availability in IBKR's product search before assuming a ticker is tradable.
- See **TAX.md** for PFU 30%, form 3916/3916-bis declarations, and broker-specific tax notes.

## Project-Specific Rules
- All strategy specs live in `data/strategies/` as JSON files
- Portfolio state is committed (needed by the sandboxed remote agent)
- Query-hash cached price data goes in `data/cache/` (gitignored)
- Every trade must have a `reasoning` field — no silent trades
- **Long-only, no short selling.** Use inverse ETFs (`bearish-etfs` universe: SH, PSQ, SQQQ, SPXS, etc.) to express bearish views as long positions. True shorts require borrow data we don't have.

## Market Data Pipeline
- **Source of truth**: `data/market/ohlcv/{SYMBOL}.jsonl` — one row per trading day, committed to git so the sandboxed remote agent can read prices without calling yfinance at runtime.
- **Populator**: `.github/workflows/fetch-ohlcv.yml`. Weekdays 22:30 UTC: full universe (~600 tickers — equities, ETFs, forex, crypto), after US market close. Weekends 19:30 UTC: `--crypto-only` subset (~30 tickers), **before** the 20:00 UTC trading session so crypto agents read fresh intraday closes. Invokes `scripts/fetch_ohlcv.py`.
- **Reader**: `engine.market_data.MarketDataFetcher` serves from the store first, falls back to yfinance only when the store doesn't cover the range. Same code path works in local dev and in the sandbox.
- **Not to be confused**: `scripts/fetch_market_data.py` writes a single benchmark snapshot (`data/market/today.json`, gitignored) for daily session commentary. Different job.

## Session Cadence (RemoteTriggers)
- **Weekday session** (`0 20 * * 1-5`): full 10-agent roster + Oracle. Runs Mon-Fri 20:00 UTC.
- **Weekend crypto session** (`0 20 * * 6,0`): crypto-capable roster only — `satoshi`, `yolo-sapiens-eur`, `yolo-sapiens-usd` + Oracle. Runs Sat-Sun 20:00 UTC. Each agent is instructed to restrict this session's orders to crypto pairs in their respective base currency (other markets in their universes are closed; broker would reject anyway). `world` stays excluded — its breadth across equities/forex/ETFs makes it a mostly-rejections agent on weekends; revisit if it ends up heavily crypto-weighted. `steady-eddie-*` mention crypto but only as thematic context, not active trading.
- Both triggers follow the same pipeline: author orders via the outbox → paper broker fills → daily log → snapshots → agent posts → Oracle blog + posts → save content → **Ring 2 journal rewrite** (every participating agent + The Oracle rewrite `data/agent_memory/{agent_id}.md` in first person) → **baselines refresh** (Step 9a — passive benchmark + coin-flip series rebuilt for every agent + global MSCI World reference; full-rewrite, idempotent) → commit `data/` and push. Same engine, different roster.
- **Cadence-invariant pipeline rule.** Any cadence (weekday, weekend, future holiday) calls the exact same `step_*` helpers in `scripts/daily_session.py`. The only difference between triggers is the `ROSTER` value passed to agent dispatch. No inline file-writing, no "verify X is current" prose substituted for a step call, no manual proxy data construction. If a step would be a no-op for the cadence, it must still be invoked — the helpers are idempotent.
- **Bundle is cadence-invariant.** `engine.output_bundle.assemble_output_bundle` always emits all 10 agents in `bundle.agents`, regardless of who ran. Non-runners get `commentary=null, trades=[], posts=[]` and their carry-forward portfolio summary. The orchestrator must build `portfolio_summaries` via `scripts.daily_session.build_portfolio_summaries()` (covers all 10 agents on disk), not by filtering on running agents.
- **Trading session has no outbound HTTP dependency.** Prices and benchmarks are read from the committed `data/market/ohlcv/` store, populated by the `fetch-ohlcv` GitHub Action cron. `scripts/fetch_market_data.py` is store-only by default — `--allow-network` is local-dev-only.
- The journal rewrite step is load-bearing: if a session's commit touches `data/posts/`, `data/blog/`, `data/output/` but NOT `data/agent_memory/*.md`, Step 9 was skipped. Sessions 2026-04-20..22 hit this bug — weekday trigger fixed on 2026-04-22.
- The baselines step is load-bearing for the site's "vs benchmark / vs coin flip" deltas — if a daily commit touches `data/portfolios/` but not `data/baselines/`, Step 9a was skipped. Same diagnosis pattern as the journals. Saturday 2026-04-25's weekend session hit this bug; weekend trigger fixed on 2026-04-26.

## Site (Ring 3a)

Public narrative at `midas.revah.paris`. Static Astro site in `site/`, deployed by Vercel on every push to `main` — including daily-session commits. No API, no live data fetching. Everything renders from committed artifacts at build time.

Pages: `/`, `/arena`, `/arena/:id`, `/journal`, `/journal/:date`, `/feed`, `/ticker/:slug`, `/simulate`, `/about`.

The `/simulate` page (signal-shape strategies for now) is a separate product from the agent narrative — visitors compose a strategy, get a real backtest from the Cloud Run service, and share results by URL. Mirror, allocation, cache, and multi-strategy overlay are deferred to subsequent plans.

See `site/README.md` for local development. Data shape assumptions live in `site/src/lib/`; the 10-agent display manifest is in `site/src/lib/roster.ts` (duplicated from `engine/posts.py` — update both if the roster changes). Ring 3b so far: trade cards (inline on trade-kind posts, joined from `data/orders/outbox` + `inbox` by `order_id`), mention chips, per-ticker history pages, time-travel archive, dark/light toggle, per-position valuations on dossiers, and per-agent baselines (passive benchmark + coin flip on dossier chart, MSCI World reference on leaderboards). Still deferred: threaded replies (agents don't emit `parent_id` yet).

The `AGENT_BENCHMARK_LABELS` map in `site/src/lib/baselines.ts` mirrors `AGENT_BENCHMARKS` in `engine/baselines.py` — update both when an agent's benchmark changes. Same convention as `roster.ts`.
