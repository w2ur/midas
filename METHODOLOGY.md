# Midas — Methodology & Limitations

*Pre-registered evaluation methodology. This document states what Midas measures, how, and — deliberately, up front — everything that is wrong or unproven about it. It is versioned in git, and every substantive change is logged, dated, in the changelog at the end of this document — the public record of what was claimed when. (The full commit history lives in the live-desk repository, which is not public today; see "The engine is open source" below.)*

Last substantive update: 2026-07-27.

---

## What Midas is

Midas is a public experiment: ten Claude trading personas, each started with €10,000 of **paper** money on **2026-04-17**, evaluate the market and journal their reasoning autonomously every weekday with no human in the loop — authoring an order only when conviction clears the bar, which most agents do on a minority of sessions (see the live cadence figures below). An eleventh agent, the Oracle, narrates but does not trade. Every order, the reasoning behind it, every fill, and every portfolio snapshot is committed to a git repository and rendered on this site. The engine that runs the desk is open source (MIT); the full live-desk repository — the complete data ledger — is not public today. The site at `midas.revah.paris` renders entirely from those committed artifacts.

This is an experiment in *process and transparency*, not a demonstration of trading skill. Read the limitations section before drawing any conclusion from a number on the leaderboard.

## What is measured

For each agent we track the EUR mark-to-market value of its portfolio over time, and its return since inception. Returns convert all positions to EUR (the operator is a French tax resident); a USD-denominated holding therefore carries a currency-translation effect — see *Known distortions* below.

## The controls (this is the part most projects skip)

A return number in isolation is meaningless. Every agent is graded against **two controls**, both rendered publicly on its dossier:

1. **Passive benchmark** — a buy-and-hold of the agent's natural reference (e.g. SPY-equivalent for a US-equity agent, BTC-EUR for the crypto agent, a flat EUR-cash line for the FX agent). Beating this is the minimum bar for "the trading added anything."
2. **Coin-flip phantom** — a portfolio that made *random* trades of the same cadence and sizing in the same universe. Beating this is the minimum bar for "the decisions were better than chance."

A global **MSCI World** reference line appears on the leaderboards as a market-wide anchor.

An agent that beats its benchmark but loses to its own coin-flip has demonstrated nothing but luck. We show both deltas next to every return so that this cannot be hidden.

## Costs

From **2026-06-13**, paper fills carry a realistic, per-asset-class fee model (`engine/fees.py`):

- **Equities / ETFs**: ~0.05% with a €1.25 per-order floor — Interactive Brokers Ireland tiered pricing.
- **Crypto**: 0.40% (Kraken spot taker; the cheaper 0.25% maker rate is *not* assumed — we use the conservative number).
- **FX**: ~0.002% spread proxy.

Fees are deducted from cash on every fill and tighten the cash-availability check. **Historical `bt` backtests (factor research and the standalone backtester service) are gross of costs**, however: `bt`'s commission hook does not expose the ticker, so the per-asset-class fee model cannot be mapped into it faithfully without a non-trivial signature adaptation. That full wiring is deferred; until then those backtests carry an explicit `GROSS_OF_COSTS` warning in the CLI output and the backtester API `warnings` field, and their returns should be read as fee-free. **Before 2026-06-13 the paper simulation modelled zero fees.** We do not retroactively rewrite history; the pre-fix period is fenced and returns from that window are fee-free and should be read as such. A separate **after-tax shadow ledger** (`data/tax_shadow/`) estimates the French flat tax (PFU, 30%) that realized gains would incur — reporting only; it never affects the portfolios. Its crypto figure is a documented per-coin approximation of the statutory global-portfolio method (see the module docstring); it will be replaced with the exact method before any real capital is deployed.

## The noise statement

**Eight weeks is statistical noise.** With roughly 35 daily observations, the variance of returns dwarfs any plausible skill signal; distinguishing skill from luck at conventional confidence would take years of data, not weeks. Accordingly:

> **No claim of skill, edge, or "Claude can trade" will be made on the basis of this experiment until at least 6 months have elapsed AND each agent has accumulated ≥100 fills — and even then, only relative to the two controls above, net of fees and modelled tax.**

This threshold is pre-registered here so that a later good run cannot be retrofitted into a skill claim. The live status against that threshold — fills accumulated per agent, the binding constraint, and whether the desk is on track — is generated below from the same data as the leaderboard, not hand-typed.

## Known distortions in the current leaderboard

These are real and we surface them rather than letting a reader discover them as a "gotcha." Figures below are illustrative, drawn from the standings around 2026-06-06; the live site carries current numbers.

- **Currency translation, not skill.** The EUR weakened against the USD over the sample. Because returns are reported in EUR, every USD-denominated book received a mechanical translation boost. For the leading agent (`sharp-shooter-usd`, ~+7.8% at the time), roughly **2.35 percentage points** of the return is this currency-accounting effect, not a trading decision.
- **Universe mix, not selection.** US equities outperformed European equities by ~6pp over the sample. A further ~3.5pp of that same agent's lead reflects *which market it was assigned*, not which stocks it picked. Net of both effects, the nominal leader and the second-place agent are within ~0.1pp of each other in native-currency terms — a statistical tie.
- **Day-one inception artifact.** One agent (`world`) recorded a first-day mark-to-market above its €10,000 baseline, inflating its reported return by an amount unrelated to subsequent decisions (true return from actual day-1 NAV is materially lower than the headline).
- **Lost fill, `sharp-shooter-eur` (2026-05-21).** A conditional-order sale was confirmed by the broker but its portfolio write was lost to an infrastructure bug; the ledger was deliberately not rewritten to correct it. Reported return is **+3.38%**; a coherent replay of the missing sale (and the resale it invalidates) lands at **+0.28%**. Full incident record in the [2026-07-27 changelog entry](#lost-fill-2026-05-21) below.
- **Survivorship bias in historical factor research.** The index universes (`sp500`, `nasdaq100`, `cac40`, `dax`, `ftse100`, `stoxx-600`) resolve to their **current** constituents, committed in `data/universes/*.json`. Backtesting one of these over a historical window silently trades only the names that survived to today — the delisted losers were never in the sample. An early factor-research run against the S&P 500's current membership from 2024 reported returns inflated by roughly **194%** versus the same run on a survivorship-free universe. We cannot reconstruct point-in-time membership without a historical-constituents feed we do not have, so the mitigation is a loud `SURVIVORSHIP_BIAS` warning (`engine/survivorship.py`) emitted by the CLI backtesters and the backtester API whenever a run's start date predates the universe file's last refresh. The factor-research and Add-a-Strategy defaults were moved to `dow30` (a stable, slow-moving 30-name universe) and `etf-broad`, which do not carry this distortion.

## How execution is disciplined

- **Safety lives in the broker, not the prompt.** The paper broker enforces 15 distinct rejection/cancel reason codes (cash, shares, notional, universe, drawdown, FX-rate, order-count, trigger-expiry, agent cancellations, and more). A persona's prompt is aspirational; the broker is what actually constrains it.
- **Decision-time air gap.** Trading sessions run with **no outbound HTTP**. Prices — and, for the two agents in the sentiment A/B below, news headlines — come only from committed stores, populated out-of-band by separate scheduled jobs. Agents never fetch the web at decision time. This makes two failure modes that affect comparable systems *structurally impossible* here: tool-level look-ahead leakage, and live-fetch instability. Headlines, when used, are pre-sanitized committed data treated as untrusted input (see the A/B section).
- **Idempotent, auditable order flow.** Orders and fills are committed JSONL keyed on deterministic order IDs; re-running a session cannot double-fill. Conditional orders carry mandatory expiries and fire through a separate watcher.

## Pre-registered experiment: sentiment A/B

*Registered 2026-06-13, before any sentiment-informed session. The published evidence on adding news/sentiment to LLM traders is mixed-to-negative (it often injects noise and raises turnover), so this is run as a falsifiable test, not a feature — and the result will be published either way.*

- **Hypothesis.** Giving an analyst a feed of recent, sanitized news headlines for the tickers it holds does **not** improve its risk-adjusted, net-of-fee return versus its own controls, and may worsen turnover.
- **Treatment group (2 agents):** `satoshi` (crypto) and `sharp-shooter-eur` (EU momentum). **Control group:** the other 8 agents, which receive no sentiment feed.
- **Mechanism.** A separate scheduled job collects ≤10 headlines/ticker/day for active tickers and commits them to `data/market/news/`. The two treatment agents read their tickers' digests during their normal (still air-gapped) session, under an explicit "untrusted data, never instructions" preamble. Headlines reach **analysts only** — never the real-money Manager path, which consumes only structured research-note fields, never raw text.
- **Window:** 4 weeks from first sentiment-informed session.
- **Metrics:** each treatment agent's return vs its own passive benchmark and coin-flip control (the same two controls every agent has), its turnover, and qualitative drift in its trade rationales — each compared against the control group over the same window.
- **Outcome:** if the treatment shows no benchmark-relative improvement (or a turnover spike), the feed is removed and the result reported as a negative finding. Sentiment is **never** promoted to the Manager path regardless of outcome. A positive result only makes more *analysts* eligible, never the Manager.

## Scope and honest limitations

- **Paper money only.** No real capital is at risk today. A real-money deployment (€1,500–2,000, Interactive Brokers Ireland + Kraken, French-tax-aware) is designed but gated: capital moves only after a separate manager strategy demonstrably beats a deterministic baseline, net of fees and tax, over a multi-month pre-registered window. Paper results are a weaker form of evidence than live trading, by definition.
- **One model family by design.** Every agent runs on Claude. This is **not** a model comparison and no cross-model claim can be drawn from it.
- **Single run, no pre-registered hypothesis test.** This is one realization of one experiment, not a controlled study with replication. Treat it as a transparent case study, not a result.
- **Small sample, short horizon.** See the noise statement.

## What you may fairly conclude today

That ten Claude agents have evaluated the market and paper-traded autonomously, every weekday since 2026-04-17 — authoring a trade only on a minority of sessions — with every decision and its reasoning committed to git and rendered on this site line by line (the engine is open source; the full live-desk repository is not public today); that each is measured against both a passive benchmark and a random-trade control shown beside its return; and that the whole thing was built and run by one person. Nothing about returns, edge, or whether "AI can trade." Those questions are deliberately deferred to the thresholds above.

<a id="open-source"></a>

## The engine is open source

The engine that runs this experiment is public at [github.com/w2ur/midas-core](https://github.com/w2ur/midas-core) under the MIT licence. That includes the **Brain/Hands** architecture — agents author orders to an outbox on disk; a paper broker enforces 15 distinct rejection/cancel reason codes and writes fills to an inbox — the `roster.yaml` config that drives the whole cast (traders, a narrator, and an allocator, each with its own safety rails enforced in the broker rather than the prompt), and a runnable demo desk with a documented walkthrough. Anyone can clone it and stand up their own desk: the quickstart in the [midas-core README](https://github.com/w2ur/midas-core#readme) is the starting point. A fuller account of the architecture, the rails, and what is and is not public is at [/open-source](/open-source).

What is **not** public is this live desk's own repository — the full data ledger of orders, fills, and snapshots. The site renders those committed artifacts, but the repository itself is private today. So the framework is open and reproducible; the specific book you are reading here is shown, not shared.

## Methodology changelog

*Methodology changes are logged here rather than silently applied — pre-registration honesty.*

- <a id="snapshot-overwrite-2026-08-03"></a>**2026-08-03 — Snapshot overwrite disclosed and reverted; the equity curve is now immutable by construction.** Portfolio snapshots are keyed on the *market* date their valuation was priced at, because the passive-benchmark and coin-flip series are dated from the price series and the two must share an axis. The write was an upsert on that key. When a session ran before its own market data landed — the OHLCV cron populates the store at 22:30 UTC, after the 20:00 UTC session — it re-used the previous close's date and **overwrote an already-published row**. On 2026-08-03 the weekday session rewrote the 2026-08-02 weekend-refresh rows for `steady-eddie-usd`, `sharp-shooter-usd`, and `world`, so a published 2026-08-02 portfolio value silently absorbed trades made on 2026-08-03 (`steady-eddie-usd` moved from €13,272.29 to €13,271.04, cash from €991.10 to €1,454.57). The same mechanism had been dropping and restamping rows since the weekend cadence began. **The three rows have been restored** to the values published on 2026-08-02, verified byte-identical against the pre-session commit `18845d8bb` — this reverts an edit the ledger never authorised, the opposite of the reconciliation refused in the entry below. Snapshots now additionally carry `session_date`, and a row may only be replaced by the same session that wrote it; a later session is refused and warns. A session whose price store has not advanced therefore records no new point rather than restating an old one, and the valuation lands on the next real close — the date it was priced at anyway.
- <a id="lost-fill-2026-05-21"></a>**2026-07-27 — Lost fill disclosed: `sharp-shooter-eur` carries a ledger artifact, deliberately not rewritten.** On 2026-05-21T21:47:43Z a conditional order (`ord_2026-05-21_sharp-shooter-eur_001`, SELL 1 ASML.AS @ €1249) fired and was confirmed by the paper broker (`status:"filled"` in `data/orders/inbox/2026-05-21.jsonl`), but the matching portfolio write never reached the repository. Cause: `scripts/check_triggers.py`'s `commit_and_push()` omitted `data/portfolios` from its `git add` pathspec, so `apply_trade()`'s mutation landed on the GitHub Actions runner's ephemeral disk and was discarded at teardown, while the inbox confirmation was pushed. The bug was live from commit `418e763bf` (2026-05-18, the watcher's introduction) to `8ff48861e` (2026-05-23, fix); exactly one conditional order fired inside that window. Reported return for `sharp-shooter-eur` today is **+3.38%** (cash €10,338.40). A coherent replay — inserting the lost sale *and* voiding the 2026-06-24 SELL 1 ASML.AS @ €1560.80 that a correctly-stated ledger would have rejected `NO_POSITION_TO_SELL` (the position it sold no longer existed) — lands at **+0.28%** (cash €10,027.85). Inserting the lost sale alone is impossible: it would mean 4 shares sold against 3 bought. **The ledger is not being rewritten.** Every published snapshot, trade, and leaderboard figure since 2026-05-21 stays exactly as executed and committed — reconciling it would delete a trade the broker genuinely confirmed and restate 38 of the 63 snapshots published since. That is the trade-off this desk exists to refuse: a track record that can be quietly edited is not a track record. The gap is now structurally guarded: `tests/test_ledger_integrity.py` diffs every filled inbox row against every portfolio's trade log on every push to `main` (`.github/workflows/session-integrity.yml`) and fails on any *new* divergence — this incident is the sole, named, greppable exception. Root cause fixed 2026-05-23, hardened 2026-06-12 with per-fire commits (removing the batch commit window entirely). The dossier at [`/arena/sharp-shooter-eur`](/arena/sharp-shooter-eur) carries a standing marker on the return figure linking back here.
- **2026-07-24 — Engine open-sourced + repository-visibility wording corrected.** The reusable engine and framework behind this experiment has been public at [`midas-core`](https://github.com/w2ur/midas-core) (MIT) since 2026-07-15 — the Brain/Hands broker, the `roster.yaml`-driven cast, and a runnable demo desk. This document previously described artifacts as "committed to a public git repository"; that phrasing is corrected to distinguish the **open-source engine** from the **private live-desk ledger**. The site still renders every committed artifact; the live repository itself is not public today. Documentation and positioning only — no data or execution behavior changed.
- **2026-07-24 — Manager made public.** The Manager's dossier — portfolio history, fills, and decision log — is now published at [`/arena/the-manager`](/arena/the-manager). This supersedes the "Manager artifacts remain private and off the public site" clause of the 2026-06-28 entry. The Manager stays **off the ranked leaderboard** — different capital, inception, and mandate, so it is not comparable to the ten traders — and its order channels stay isolated from the public inbox. Rationale: the experiment's transparency principle extends to the allocator now that the allocator architecture itself is open in the core.
- **2026-07-03 — Trigger-rails documentation corrected.** Docs previously said conditional-trigger fires apply "the same safety rails" as market orders. In fact the watcher path deliberately skips two *batch-level* rails — `MAX_ORDERS_PER_DAY` and `DAILY_DRAWDOWN_HALT` — while keeping every order-level rail (notional, universe, cash/position/shares, FX, apply_trade). Documentation-only fix (CLAUDE.md, README); no rail behavior changed.
- **2026-07-03 — Backtest honesty + reproducibility.** `bt` backtests are flagged gross of costs (`GROSS_OF_COSTS` warning in the CLI and API; full fee-model wiring deferred because bt's commission hook lacks the ticker). The `random` selector is now seeded deterministically from (strategy id, window start) so factor-research runs reproduce run-to-run instead of reading numpy's global RNG. The CLI `--to` default changed from a hardcoded date to today. Factor-research output is stamped with `generated_at`, the git SHA, and the run arguments.
- **2026-07-03 — Survivorship-bias guard.** Index universes resolve to current constituents, so historical factor-research runs were survivorship-biased (an S&P 500 run from 2024 was inflated ~194%). Added a `SURVIVORSHIP_BIAS` warning (`engine/survivorship.py`) surfaced by `scripts/run_backtest.py`, `scripts/run_all_combos.py`, and the backtester API `warnings` field whenever a run starts before the universe file's last refresh. Moved the factor-research and Add-a-Strategy defaults from `sp500` to `dow30`/`etf-broad`. Reporting-only: no historical result is rewritten.
- **2026-06-28 — Manager recalibrated.** Conviction gate lowered 7→6; acts on a single coherent high-conviction thesis rather than requiring multi-analyst consensus; conditional (trigger) orders enabled for the Manager channel. Rationale: the prior configuration produced an all-cash book across 6 review sessions (conviction 3–6, never reaching the gate), which cannot yield an evaluable Gate C track record. These changes restore the Manager's ability to act on confirmation without front-running, while preserving discipline (HOLD remains default; gate still enforced in code). Manager artifacts remain private and off the public site.
- **2026-06-13 — Fee model introduced.** Paper fills now carry a realistic per-asset-class fee model (`engine/fees.py`). Pre-fix period returns are fee-free and fenced as such. After-tax shadow ledger (`data/tax_shadow/`) added for PFU 30% drag reporting.

## Colophon

- **Cadence.** Weekdays at 20:00 UTC the full ten-agent roster trades; the Oracle narrates. Weekends run a valuation-only refresh — no agents, no new trades — so the leaderboard stays current without manufacturing weekend activity.
- **Execution.** A paper broker enforces 15 distinct rejection/cancel reason codes on every order, and the conditional-order watcher adds a sixteenth (`TRIGGER_EXPIRED`). Safety lives in the broker, not the prompt.
- **Stack.** A Python engine (`bt` for deterministic strategies, `yfinance` for historical data, Claude agents for the analytical ones); the paper broker; and this site — Astro, static output, rendered entirely from committed artifacts and deployed on Vercel.
- **Author.** Built and run by William — [william.revah.paris](https://william.revah.paris).
