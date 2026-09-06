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
uv venv --python 3.12
uv pip install -r requirements.txt
```

**`uv`, not `python -m venv` + `pip`.** There is no bare `python` or `pip` on
this machine's PATH, so the previous instructions could not be followed as
written; a bare `python3` would have resolved to Homebrew's dependency
interpreter, which is the invisible-interpreter-swap the portfolio rule warns
about. Note this is deliberately **not** `uv sync`: `requirements.txt` below is
the resolved lockfile every consumer installs, and adding `uv.lock` would give
the project two answers to what it depends on. The `python scripts/...` lines
elsewhere in this file assume an activated venv (which is the case in CI and in
the cloud sandbox); locally, prefer `.venv/bin/python`.

**Dependencies are pinned.** `requirements.in` holds the human-editable loose
constraints; `requirements.txt` is the fully-resolved **lockfile** that every
consumer installs (6 GitHub workflows, the backtester Dockerfile, local dev,
the sandbox). Pinning the full transitive closure makes CI reproducible — a
freshly-published wheel can't break a previously-green run without an explicit
lock bump (origin: the 2026-06-28 pandas 3.0.4 segfault that an unpinned `>=`
let in). To add/change a dep: edit `requirements.in`, then regenerate with
`pip-compile --strip-extras -o requirements.txt requirements.in` (or seed from
the `Successfully installed …` line of a green CI run), and commit both files.

## Testing
```bash
pytest -q            # tests/ AND backtester/tests — both are in `testpaths`
cd site && npm test  # the vitest suite; `npm test` is the authority on the count
```

`.github/workflows/tests.yml` runs each suite as its own job behind a final
`gate` job, which is the one a branch protection rule would require. **`gate`
asserts a NAMED list**, held in the workflow's own `EXPECTED` — the roster is
that list and is never restated here — rather than "did anything in `needs`
fail?". Actions reports a skipped job as `skipped` and an absent one as nothing,
and `jq 'all(.[]; .result=="success")'` over an empty set returns `true` — so
the short form reports success on zero coverage. **Add a suite and you must add
its job id to `EXPECTED`**; `tests/test_ci_guards.py` makes that mechanical.
`gate` is the only check a branch protection rule should require — requiring the
individual jobs reintroduces the hole.

**A required status check on `main` breaks the desk** (measured 2026-08-24..09-04):
`github-actions[bot]` is not exempt from it, every scheduled writer's push is
refused with `GH006`, and the watcher's `triggers/*` fallback cannot merge either
— it pushes with the same token — so fills stay durable and visible, not landed.
`gate` is advisory **by decision**; never add a required check, never add a PAT.
Dates and the removal command are in the **`midas-ci-guards`** skill.

Warnings are errors, with three named third-party exceptions in `pyproject.toml`. Hypothesis settings live in one profile (`tests/conftest.py`, `midas`): `deadline=None` **by measurement** — write no per-test `@settings`.

**Every guard must be able to fail, and must have a consumer.** A check nobody reads and a check that cannot go red are the same thing. The rest of the CI discipline — path filtering, push-with-retry, attestation dating, `sync_core.check()`'s two tiers, failure-issue alerting — is in the **`midas-ci-guards`** skill.
**Published data is guarded in CI, not only in application code** —
`session-integrity.yml` runs ledger-integrity, ledger cash-replay, baseline
freshness (one-sided, reads the published *state*, never the diff) and
append-only on every push to main. **A dated row in `data/portfolios/*/snapshots.json`
or `data/baselines/**` that already exists at `HEAD^` must be byte-identical at
`HEAD`, or the commit message carries `[restate]`** — `git log --grep='\[restate\]'`
is the complete list of every time the published record moved. Mechanics and
calibration are in the **`midas-ci-guards`** skill.

**Restatement requires disclosure up front** (`engine/disclosure.py`).
`restate_valuations.py --apply` and `restate_bundles.py --apply` refuse to run
without `--changelog-entry <anchor>`, and verify the anchor resolves to a real
`<a id>` in METHODOLOGY.md. Dry runs are ungated — you cannot write the
disclosure before you know what would move. Origin: the 2026-08-02 ledger
rewrite that moved a book's published return from +3.30% to +0.19% with no
changelog entry, found five days later by an unrelated cross-check. A
precondition, not an audit: an audit tells you afterwards that you forgot.

**Restatement scope is a set, not a bool.** `build_all_baselines(restate_series=…)`
takes `{"coinflip"}`, `{"benchmark"}` or `{"<agent>/<kind>"}`. The bool it
replaced could only say "everything", which on 2026-08-07 moved eight passive
benchmarks that should not have moved (on fresher *prices*, not units) and they
had to be restored by hand.

**One missing-price policy** (`engine.valuation.value_position`). Snapshots used
to fall back to `avg_cost`, the leaderboard valued at **zero**, and restatement
raised — same book, same missing row, three published answers, two of them
numbers. All three now refuse and name the condition in the broker's own
vocabulary (`NO_PRICE_DATA` / `NO_FX_RATE` / `CURRENCY_UNRESOLVED`).

**`attest_ledger.py --verify` must compare against the previous `attest/*` tag's
own tree** — a digest computed and not compared was 55 green days of nothing.

`fetch_ohlcv.py`'s deliberate non-zero exits, and why its 10% threshold is measured against store coverage rather than the requested universe, are in the **`midas-market-data`** skill.
## Dashboard
```bash
streamlit run app/main.py
```

## Project Structure

Paths whose purpose is not obvious from the name. Everything under `engine/` that
is not listed is ordinary code; read it rather than a description of it.

- `engine/orders.py` — Order/Fill types + outbox/inbox JSONL serde (the Brain/Hands primitive)
- `engine/paper_broker.py` — Hands side: 19 rejection/cancel reason codes, fill logic, portfolio update. **It refuses implausible numbers, not just ill-formed ones** — `PRICE_IMPLAUSIBLE`, `TRIGGER_LEVEL_IMPLAUSIBLE` (refused *at intake*, so it never arms), `CURRENCY_UNRESOLVED`, `VALUATION_UNAVAILABLE`. **An unevaluated rail is not a passed rail.** The bands are deliberately loose: they target unit/basis errors, which arrive as a factor of 100, not market moves — and `tests/test_rails_live_coverage.py` replays the committed ledger so a tightened band cannot start refusing real trades unnoticed.
- `engine/market_data.py` — **store first, per ticker; yfinance only for what the store cannot cover**
- `engine/quotes.py` — ticker → currency in three ordered layers (override map, vendor's captured answer, suffix heuristic), then price reads. **The heuristic returns `None` for a suffix it does not enumerate rather than defaulting to USD** — a wrong currency still prices, so that failure has no symptom. **`GBp` is a unit, not a currency: the store is ISO-denominated and the pence→pounds division happens ONCE, at ingest. Read paths must never scale**, or every LSE price is divided by 100 twice.
- `engine/corporate_actions.py` — split detection, keyed on a transition-anchored constant ratio
- `engine/agent_memory.py` — Ring 2 per-agent journal I/O
- `engine/persona_dispatch.py` — loads `.claude/agents/{id}.md` and wraps a task prompt with the persona body, and injects any in-window desk notice between the two
- `engine/config.py` — `MidasConfig`, the single source of truth for paths, roster and safety rails, loaded from `roster.yaml`; `MIDAS_DATA_DIR`-aware
- `engine/output_bundle.py` — assembles `data/output/YYYY-MM-DD.json`, the single source of truth for API + retries
- `engine/universes/` — universe resolvers. **Read from committed `data/universes/*.json`; no network at runtime.**
- `roster.yaml` — the cast: agents, voices, schedule, universes, benchmarks, per-agent safety rails. **`max_order_notional_pct` is a percentage of current book value and takes precedence over the absolute `max_order_notional`**, re-scaling as the book moves; they were once `1_000_000 / 100 / -95` on €10,000 books, a per-order cap 100x the whole portfolio.
- `scripts/session_state.py` — resumable step markers, **scoped to the session anchor's `base_sha`, not just the UTC date**
- `scripts/prompt_hash.py` — hashes the fenced prompt block in `docs/triggers/weekday-session.md`, excluding the self-referential `PROMPT_SHA256:` line
- `scripts/bootstrap_venv.sh` — builds the Python 3.12 venv at image-build time, or verifies it (`--check`) in Step 0
- `data/portfolios/`, `data/orders/`, `data/agent_memory/`, `data/baselines/`, `data/universes/` — **all committed**, because the remote agent runs sandboxed and cannot fetch
- `data/orders/dropped/` — Brain-side audit ledger for agent trades that were not valid orders
- `data/orders/{manager-pending,manager-cancels,manager-inbox}/` — the Manager channel, isolated from the trader channel
- `data/agent_config/` — `live_switch.json` only; per-agent rails moved to `roster.yaml`
- `data/desk_notices.json` — dated desk-wide notices (`{id, from, until, audience, text}`), read by `engine/desk_notices.py` and injected into every persona prompt at dispatch. **The only channel that tells the agents a fact about the desk's own machinery without the owner re-pasting the live RemoteTrigger prompt** — `docs/triggers/weekday-session.md` is hashed, so a two-week fact does not belong there. Windows are inclusive and self-retiring; `audience: "traders"` is `role: trader` only, `"all"` is everyone. Missing or malformed → no block and a logged warning, never an exception: losing a session over desk prose is worse than an agent missing the notice.
- `data/cache/` — query-hash cached price data (gitignored)
- `.claude/agents/` — the ten trader personas plus `the-oracle.md`, which narrates and does not trade
- `site/` — Astro static site (Ring 3a), `midas.revah.paris`; reads `data/` and `.claude/agents/` at build time. See the **`midas-site`** skill.
- `backtester/` — FastAPI service on Cloud Run wrapping `engine.backtest.run_backtest`; being spun out as its own product
- `app/` — Streamlit dashboard pages

Full rail bands, the currency-resolution layers and the unit migration are in the **`midas-rails-and-currency`** skill.

## Repo Split (SP4 mirror + SP5 publish-prep)

- **`w2ur/midas-core` (PUBLIC, MIT) is a MIRROR of this repo's engine, reusable orchestration and `examples/demo-desk`**, produced by `scripts/sync_core.py`.
- **Edit here, then `python scripts/sync_core.py apply --core <checkout>`. Never hand-edit midas-core.** `core-drift-guard` enforces it. This is also why midas-core carries no `CLAUDE.md`: guidance placed there would invite the one mistake the mirror discipline forbids.
- **`CLAUDE.md` is not in the manifest** — `sync_core.py` never mentions it, and midas-core has no copy. Editing this file needs no mirrored counterpart.
- `check()` runs over the full `apply_manifest`, in two tiers, because some manifest files are rescraped or rewritten on a schedule. Details in the **`midas-ci-guards`** skill.
## Infrastructure
- **Cloud Run exception (backtester).** The backtester service runs on Google Cloud Run — a deliberate exception to this project's zero-cost hosting default. Rationale: it is a heavyweight Python container (`bt` + pandas + `engine/`) that edge runtimes (Cloudflare/Vercel/Netlify) cannot host; Cloud Run scales to zero (`min-instances=0`, `max-instances=3` = $0 idle, capped abuse) with 2–5s cold start. Secured with an app-layer shared-secret gate (`BACKTESTER_SECRET`, checked by the FastAPI app) plus `--max-instances=3`; IAM stays open (`--allow-unauthenticated`) so the Netlify proxy can reach it without GCP credentials (SP3). Hosting is revisited at SP4 during the repo split.


## Architecture Principle — Brain / Hands

All external-world integrations in Midas follow a **Brain / Hands split**:

- **Brain** — the Claude Code sandbox (daily trigger cron). Reads from disk, authors decisions (trades, posts, journal entries), writes to an outbox on disk. Holds no external credentials.
- **Hands** — separate workers (paper broker for simulation, future real-broker worker for live execution). Read outbox, validate against safety rails, execute, write confirmations to inbox on disk. Pure executors.

First application (Ring 1): trade execution.
- Agents write orders to `data/orders/outbox/YYYY-MM-DD.jsonl`.
- `engine/paper_broker.py` enforces 19 rejection/cancel reason codes (safety checks), fills at end-of-day close from the OHLCV store, writes to `data/orders/inbox/YYYY-MM-DD.jsonl`.
- Fills with `status="filled"` mutate portfolios via `PortfolioManager.apply_trade`; rejections carry a reason code.
- Every fill (filled or rejected) is stamped with `executed_sha` — the git HEAD commit the broker executed against. Tamper-evident provenance: `git checkout <executed_sha>` re-derives the exact outbox order and price store the broker saw. Resolved by `engine.paper_broker._current_commit_sha`, degrades to `null` (omitted from JSONL) outside a git repo. Covers both `fill_day` and watcher trigger-fires.
- Paper fills carry a realistic per-asset-class fee model (`engine/fees.py`, IBIE/Kraken/FX rates). **The equity commission floor is a EUR amount and is converted into the book's currency** (2026-08-07, W7.4) — it was charged as a bare 1.25 on the USD books, ~8% light on every order small enough for the floor to bind. Rates are percentages of an already-converted notional and are not touched; an unavailable FX rate falls back to the unconverted floor rather than raising (bounded to cents, and on the broker path the rate is necessarily present already). An after-tax shadow ledger (`engine/tax_shadow.py` → `data/tax_shadow/`) estimates PFU drag as a reporting signal — it does not alter portfolio cash.

**Safety rails live in the Hands, not agent prompts.** The agent persona is aspirational; the broker is enforcing.

Real-money transition is a broker swap: replace `paper_broker.py` with an `ibie_broker.py` that talks to Interactive Brokers — same outbox/inbox contract, credentials held outside the sandbox. See `~/.claude/plans/2026-04-17-midas-public-experiment-design-v2.md` for the full experiment design.


### Conditional Triggers (extension of Brain/Hands)

Agents may author conditional orders that defer execution until a price condition fires. Authoring is Brain; evaluation and firing are Hands. Mechanics — the watcher, the Cloudflare crypto gate, the cancellation channel, the ops table — are in the **`midas-session-cadence`** skill. These rules outrank any change to them:

- **Expiry is mandatory** (`TRIGGER_NO_EXPIRY`) and inclusive, and **the daily 13:00 UTC sweep is its SOLE owner.** The crypto pass filters non-crypto orders out in `run()`, never in `_process_channel`, precisely so an hourly pass cannot retire an equity order early.
- **A triggered fire applies the order-level rails and DELIBERATELY SKIPS the two batch-level ones** — `MAX_ORDERS_PER_DAY` and `DAILY_DRAWDOWN_HALT`. A fire the drawdown halt would have stopped still fills, and the agent reacts next session. Changing that is a money-path decision, not a cleanup.
- **`SESSION_START` is the one constant; both windows are functions of it — move it and move them, in the same change.** The blackout end has now been wrong in both directions (20:30 too early, then 21:30 after a session move and back). **The watcher's evaluation blackout and the auto-merge deferral are DIFFERENT functions of it**: `in_blackout` opens five minutes early for the watcher's multi-minute fire path, `merge_deferred` opens at the session start because a merge is one fetch-merge-push — and deferring the merge over those five minutes was itself enough to trap a fallback branch permanently. **The merge has a hard deadline of that session**: past it the branch is stale by construction and becomes a human decision, not a self-heal. A blackout only narrows the race; `session_guard` is the correctness mechanism.
- **The crypto gate may only over-dispatch, never under-dispatch**, and **its PAT is a dead-man switch**: when the token dies the Worker fails *and* cannot report it, because the failure issue uses the same dead credential. The renewal date belongs in a calendar.
- **Gate C**: before the Manager's track record gates real money, record the commit SHA the window was scored at, the ledger basis by name, and that the decision prompt was rendering currency-labelled position values — none is reconstructable afterwards, and all three go in the METHODOLOGY changelog, not a working note.
- **Manager channel isolation.** Manager orders route to `manager-pending`/`manager-inbox`, never the public channels; Manager fills stay out of the ranked leaderboard by design. Its public dossier at `/arena/the-manager` is **unranked** and never enters `current.json`.
- **A fire that `main` refuses goes to a per-run `triggers/*` branch that `auto-merge-session.yml` merges** — a warning (exit 0), not a failure; only a commit that reaches neither is lost with the runner, and **a run that finds an unmerged `triggers/*` branch refuses to evaluate** rather than re-fire the same order at a new price against a `main` that lacks the fill. Once a run has fallen back it **stays on the branch — no rebase after a sha has reached origin** (the fills' `executed_sha` provenance). The merge **defers over the session window** (it is the push), **refuses a fill whose order or whose BOOK `main` has since touched** — per book, not per file, because a session writes `snapshots.json` and not the `portfolio.json` a fill writes, so a file-level test let a branch merge behind a snapshot valued without it, and snapshots are immutable — and resolves a conflict on `current.json` alone to main's copy. **Every check binds the `origin/main` sha it was computed on**: the merge refuses any other tip and never retries, because a rejected push means main moved, which is exactly the state nothing has checked. **The merge is dispatched before the watcher evaluates, not only after**, or a branch waiting from yesterday costs that run its fires and its expiries too; the checkout is re-anchored between the two, and **a run whose `origin/main` is not its own `HEAD` refuses to evaluate** — a merge landing between checkout and evaluation is how the same order gets fired twice. **A merge that fails transiently is retried by `retry-fallback-merges.yml`, not by "the next watcher run"** — no watcher runs between the 13:00 sweep and the session, and past the session the branch is stale by construction.

Same Brain/Hands invariant: safety rails live in the broker, at market-fill time and at trigger-fire time, not in the persona.

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
- **Share comparisons use a 1e-9 epsilon, not `==`** (`engine.portfolio._DUST_SHARES`, matching `engine.restatement`). 32 of the 227 committed trades are fractional, and `0.3 - 0.1 - 0.2` is 5.55e-17, not zero — so exact comparison left phantom dust positions open forever *and* refused an agent selling the full 0.2 it genuinely held. Both sides of that comparison were wrong; the second was found by the test written for the first.
- **A sale may not drive cash negative.** The equity fee has a floor, so a tiny disposal can cost more than it raises; `apply_trade` now refuses, symmetric with the BUY-side insufficient-cash guard. The broker's rails normally prevent this, but restatement and baseline paths call `apply_trade` directly.
- **A malformed line in the inbox raises.** `inbox_order_ids` is the only thing between an order and a second fill; it used to skip unparseable lines silently, reasoning that a corrupt line "cannot retroactively cause a double-fill if the original write succeeded" — which confuses the write succeeding with the record being readable. A corrupt override map or ticker registry still degrades to `{}` (fail-closed: unresolvable tickers are refused at the broker) but now logs an error, because that degradation silently demotes ~1,000 tickers to the suffix heuristic.
- **Long-only, no short selling.** Use inverse ETFs (`bearish-etfs` universe: SH, PSQ, SQQQ, SPXS, etc.) to express bearish views as long positions. True shorts require borrow data we don't have.


## Market Data Pipeline

- **Source of truth**: `data/market/ohlcv/{SYMBOL}.jsonl`, one row per trading day, **committed to git** — the sandboxed session has no outbound HTTP.
- **Every read path takes the raw `close`; nothing reads `adj_close`.** This is a money-path invariant, not a preference.
- **Restating a baseline series requires a changelog anchor too** — a restatement without disclosure is the failure mode the append-only gate exists to catch.
- **`end` is YESTERDAY, never today.** A mid-day manual run writes no partial bar at all.
- **`merge_rows` is the only place holding the ingest anomaly tripwire.** A quarantined row is adjudicated against the vendor's own action calendar, never waved through.
- **Not to be confused**: `scripts/fetch_market_data.py` writes a single benchmark snapshot; `scripts/fetch_ohlcv.py` populates the store.

Schedules, the revision window, corporate actions and split detection, the unit-migration stamp, Yahoo's holes and the exit-code contract are in the **`midas-market-data`** skill.

## Session Cadence (RemoteTriggers + Workflows)

- **Snapshots are keyed on the market date and are immutable across sessions.**
- **Cross-currency positions must be converted before summing, on EVERY pricing path.** A book holding more than one currency is silently wrong otherwise.
- **The session refuses to price against a store that stopped advancing** (`assert_session_fresh`). A stale store is unknown, never healthy.
- **The trading session has no outbound HTTP dependency.** Prices and benchmarks come from the committed store. Anything that adds a network call to the session path breaks the sandbox contract.
- **Bundle is cadence-invariant** — `assemble_output_bundle` always emits all 10 agents, whatever ran.

Which cadence runs when, the watchdog, the sentiment A/B, persona dispatch, model assignment and the push path are in the **`midas-session-cadence`** skill.

## Site (Ring 3a)

Astro static site at `midas.revah.paris`, built from `data/` and `.claude/agents/`. See the **`midas-site`** skill before touching anything under `site/`.
