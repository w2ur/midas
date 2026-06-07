# Midas — AI Fund Manager

## Project Overview
Personal AI fund manager that autonomously analyzes markets, makes investment decisions, and manages portfolios. Two execution engines: bt (Python) for deterministic strategies, Claude agents for analytical ones. Public narrative at `midas.revah.paris` (Astro site, Ring 3a). Streamlit dashboard for local exploration. Backtester API on Google Cloud Run.

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
- `engine/persona_dispatch.py` — load `.claude/agents/{id}.md`, strip frontmatter, wrap a task prompt with the persona body so the orchestrator can dispatch via `subagent_type="general-purpose"` (project agents are not auto-registered as dispatchable subagent types)
- `engine/selectors/` — bt Algos for entry signals (golden cross, RSI, etc.)
- `engine/managers/` — bt Algos for position management (grid, trailing stop, etc.)
- `engine/universes/` — Universe resolvers (S&P 500, congressional, crypto, etc.). Read from committed `data/universes/*.json` — no network at runtime. Refresh via `scripts/refresh_universes.py` or the weekly `refresh-universes.yml` workflow.
- `scripts/` — CLI entry points for backtesting and daily sessions
- `app/` — Streamlit dashboard pages
- `data/strategies/` — Strategy spec JSON files
- `data/portfolios/` — Runtime portfolio state (committed — needed by the sandboxed remote agent)
- `data/agent_config/` — per-agent safety rails (committed)
- `data/ticker_currencies.json` — ticker → ISO currency override map (committed)
- `data/orders/{outbox,inbox}/` — Brain/Hands trade flow (committed)
- `data/agent_memory/` — Ring 2 per-agent journals, 11 markdown files, first-person + biased, rewritten each session (committed)
- `data/baselines/` — per-agent passive benchmark + coin-flip phantom portfolios, plus `global/msci_world.json`; same snapshot shape as `data/portfolios/`; written by `scripts/backfill_baselines.py` (one-shot) and refreshed by Step 9a of the daily session (committed)
- `data/universes/` — committed index/alt universes (sp500, dow30, nasdaq100, cac40, dax, ftse100, stoxx600, congressional, insider, high-short). File presence is authoritative; resolvers never hit Wikipedia at runtime. Refresh out-of-band via `scripts/refresh_universes.py` or the weekly workflow.
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

### Conditional Triggers (extension of Brain/Hands)

Agents may author conditional orders that defer execution until a price condition fires. Authoring (Brain) happens in the normal daily session; execution (Hands) happens in a separate cron worker:

- Agent emits `{"trigger": {"op": ">=", "level": N}, "expires": "YYYY-MM-DD"}` on any trade in their session output.
- `engine/paper_broker.py:fill_day` recognizes the `trigger` field and routes the order to `data/orders/pending/{order_id}.json` instead of filling immediately.
- `.github/workflows/check-triggers.yml` runs `scripts/check_triggers.py` every 15 minutes. The watcher evaluates each pending order's trigger against live prices (ccxt for crypto, OHLCV store for equity/FX) and fires through `engine/paper_broker.execute_triggered_order`, which applies the same safety rails as market orders.
- Cancellations live as a separate channel: `data/orders/cancels/YYYY-MM-DD.jsonl`. Agents emit a `cancels: [{target_order_id, reasoning}]` field alongside their `trades`. The broker processes cancels at the start of `fill_day`, removes the target pending file, and writes a `CANCELLED_BY_AGENT` rejection to inbox.
- The watcher is blacked out 19:55–20:30 UTC to avoid commit-races with the 20:00 UTC daily session.
- Supported trigger ops (v1): `>=`, `<=`. Expiry is mandatory; orders without `expires` are rejected at the broker with `TRIGGER_NO_EXPIRY`. Expiry is inclusive — an order with `expires=2026-05-17` is `TRIGGER_EXPIRED` on 2026-05-17.

Same Brain/Hands invariant: safety rails live in the broker (now both at market-fill time and trigger-fire time), not the persona.

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

## Session Cadence (RemoteTriggers + Workflows)
- **Weekday session** (RemoteTrigger, `0 20 * * 1-5`): full 10-agent roster + Oracle. Runs Mon-Fri 20:00 UTC. Authors orders, fills, journals, baselines, posts, blog, snapshots, and writes `data/leaderboard/current.json` as the final orchestrator step (Step 9b in the trigger prose).
- **Weekend refresh** (GitHub Action `refresh-leaderboard.yml`, `0 20 * * 6,0`): valuation-only — no agents, no Oracle, no journals, no posts. Calls `step_fetch_market_data → step_update_snapshots → step_build_baselines` then writes `data/leaderboard/current.json` and commits `chore: weekend refresh YYYY-MM-DD`. Replaced the prior weekend RemoteTrigger session on 2026-05-23 — crypto agents preserve weekend exposure via Friday-authored conditional orders that fire through the watcher.
- **Trigger watcher** (GitHub Action `check-triggers.yml`, every 15 min): fires pending conditional orders, mutates portfolios, and opportunistically refreshes `data/leaderboard/current.json` (wrapped in try/except — derived state, never fatal to the fill). Blackout 19:55-20:30 UTC to avoid commit-races with the weekday session.
- **Session-watchdog** (GitHub Action `session-watchdog.yml`, daily 02:00 UTC): checks that yesterday's expected commit (`chore: weekday session` or `chore: weekend refresh`) actually landed on `main`. Catches the 2026-05-08 class of bugs where a sandbox push 403'd and the work never reached main. Failure alerts via standard GitHub workflow-failure email.
- **Live leaderboard artifact.** `data/leaderboard/current.json` is the single source of truth for the homepage live-leaderboard widget. Written by three paths: weekday session (always), weekend refresh (always), watcher fire (opportunistic). Includes `updated_at` and a `trigger` label so the site can show provenance. Per-day output bundles (`data/output/YYYY-MM-DD.json`) remain weekday-only and drive the per-date archive pages.
- **Session-mode pipeline rule.** Agent-running cadences (weekday today, future holiday-aware cadences) call the exact same `step_*` helpers in `scripts/daily_session.py`. The weekend refresh is a different category — a valuation-only refresh, not a session — and composes a strict subset of those helpers. The journal/posts/blog steps belong to sessions only; baselines + snapshots + `current.json` belong to both. `session-integrity.yml` enforces the per-mode integrity contract. **Asymmetry on `current.json`:** for *session* commits it is a non-fatal **warning** (derived, self-healing state — the cloud orchestrator routinely lands it in a follow-up `… — leaderboard` commit, and the watcher/next session reconcile it within minutes; journals + baselines stay hard failures); for *weekend-refresh* commits a missing `current.json` is a **hard failure**, since writing it is that job's entire purpose. This demotion (2026-06-07) ended a recurring per-commit false failure on the intermediate session commit (4× May–Jun 2026).
- **Bundle is cadence-invariant.** `engine.output_bundle.assemble_output_bundle` always emits all 10 agents in `bundle.agents`, regardless of who ran. Non-runners get `commentary=null, trades=[], posts=[]` and their carry-forward portfolio summary. The orchestrator must build `portfolio_summaries` via `scripts.daily_session.build_portfolio_summaries()` (covers all 10 agents on disk), not by filtering on running agents.
- **Trading session has no outbound HTTP dependency.** Prices and benchmarks are read from the committed `data/market/ohlcv/` store, populated by the `fetch-ohlcv` GitHub Action cron. `scripts/fetch_market_data.py` is store-only by default — `--allow-network` is local-dev-only.
- The journal rewrite step is load-bearing: if a session's commit touches `data/posts/`, `data/blog/`, `data/output/` but NOT `data/agent_memory/*.md`, Step 9 was skipped. Sessions 2026-04-20..22 hit this bug — weekday trigger fixed on 2026-04-22.
- The baselines step is load-bearing for the site's "vs benchmark / vs coin flip" deltas — if a daily commit touches `data/portfolios/` but not `data/baselines/`, Step 9a was skipped. Same diagnosis pattern as the journals. Saturday 2026-04-25's weekend session hit this bug; weekend trigger fixed on 2026-04-26.
- **Universe data is committed, not cached.** Step 9 (baselines) calls `engine/universes/*` resolvers which need ticker lists for sp500/cac40/dax/ftse100/stoxx600. Those lists live in `data/universes/*.json` (committed). The previous design kept them in `data/cache/universes/` (gitignored, 24h TTL) and tried to refresh from Wikipedia on cache miss — Apr 29 sandbox session needed a manual workaround because outbound HTTP is blocked. Refreshes happen out-of-band only.
- **Persona dispatch substrate.** Project-level subagents in `.claude/agents/*.md` are NOT auto-registered as dispatchable `subagent_type` values (neither locally nor in cloud RemoteTrigger sessions — Apr 29 weekday session aborted on this). Every persona-authored output dispatches through `subagent_type="general-purpose"` with the persona body injected by `engine.persona_dispatch.wrap_persona_prompt(agent_id, task_prompt)`. The orchestrator NEVER inline-authors persona content — wrapping is the substitute for the auto-registration we don't have.
- **Oracle runs on Sonnet, traders on Opus.** Same Apr 29 session: the Oracle dispatch on Opus repeatedly hit the cloud streaming idle timeout (~60s) while the model was still in pre-output thinking. Sonnet starts streaming in 2-10s and is more than capable of the daily narrative voice. Frontmatter `model:` in `.claude/agents/*.md` controls this; only `the-oracle.md` is on `sonnet`. Trade-round dispatches (single-agent reasoning, smaller prompts) stay on Opus. The Oracle prompt is also trimmed at the source (commentary capped at 240 chars/agent, trade reasoning at 100 chars, journal digest at 250 chars/agent) to keep first-token latency low — see `engine/blog.py` and `engine/agent_memory.format_oracle_digest`.
- **Push path with sandbox-branch fallback.** The trigger ends with `step_git_commit_push()`, which tries `git push origin HEAD:main` first. On 2026-05-08 the harness started 403'ing main pushes from cloud sandboxes (Day 20 commit landed only on `claude/happy-goldberg-VlIfz`, manually merged after); the helper now falls back to `git push origin HEAD` and `.github/workflows/auto-merge-session.yml` takes the merge to main. The auto-merge workflow re-runs the `session-integrity` checks before merging — same artifact rules as the on-main guard — and deletes the sandbox branch after success.

## Site (Ring 3a)

Public narrative at `midas.revah.paris`. Static Astro site in `site/`, deployed by Vercel on every push to `main` — including daily-session commits. No API, no live data fetching. Everything renders from committed artifacts at build time.

Pages: `/`, `/arena`, `/arena/:id`, `/journal`, `/journal/:date`, `/feed`, `/ticker/:slug`, `/simulate`, `/about`.

The `/simulate` page (signal-shape strategies for now) is a separate product from the agent narrative — visitors compose a strategy, get a real backtest from the Cloud Run service, and share results by URL. Mirror, allocation, cache, and multi-strategy overlay are deferred to subsequent plans.

See `site/README.md` for local development. Data shape assumptions live in `site/src/lib/`; the 10-agent display manifest is in `site/src/lib/roster.ts` (duplicated from `engine/posts.py` — update both if the roster changes). Ring 3b so far: trade cards (inline on trade-kind posts, joined from `data/orders/outbox` + `inbox` by `order_id`), mention chips, per-ticker history pages, time-travel archive, dark/light toggle, per-position valuations on dossiers, and per-agent baselines (passive benchmark + coin flip on dossier chart, MSCI World reference on leaderboards). Still deferred: threaded replies (agents don't emit `parent_id` yet).

The `AGENT_BENCHMARK_LABELS` map in `site/src/lib/baselines.ts` mirrors `AGENT_BENCHMARKS` in `engine/baselines.py` — update both when an agent's benchmark changes. Same convention as `roster.ts`.
