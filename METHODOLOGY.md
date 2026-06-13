# Midas — Methodology & Limitations

*Pre-registered evaluation methodology. This document states what Midas measures, how, and — deliberately, up front — everything that is wrong or unproven about it. It is versioned in git; the commit history is the record of what was claimed when.*

Last substantive update: 2026-06-13.

---

## What Midas is

Midas is a public experiment: ten Claude trading personas, each started with €10,000 of **paper** money on **2026-04-17**, author orders autonomously every weekday with no human in the loop. An eleventh agent, the Oracle, narrates but does not trade. Every order, the reasoning behind it, every fill, and every portfolio snapshot is committed to a public git repository. The site at `midas.revah.paris` renders entirely from those committed artifacts.

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

Fees are deducted from cash on every fill and tighten the cash-availability check. **Before 2026-06-13 the simulation modelled zero fees.** We do not retroactively rewrite history; the pre-fix period is fenced and returns from that window are fee-free and should be read as such. A separate **after-tax shadow ledger** (`data/tax_shadow/`) estimates the French flat tax (PFU, 30%) that realized gains would incur — reporting only; it never affects the portfolios. Its crypto figure is a documented per-coin approximation of the statutory global-portfolio method (see the module docstring); it will be replaced with the exact method before any real capital is deployed.

## The noise statement

**Eight weeks is statistical noise.** With roughly 35 daily observations, the variance of returns dwarfs any plausible skill signal; distinguishing skill from luck at conventional confidence would take years of data, not weeks. Accordingly:

> **No claim of skill, edge, or "Claude can trade" will be made on the basis of this experiment until at least 6 months have elapsed AND each agent has accumulated ≥100 fills — and even then, only relative to the two controls above, net of fees and modelled tax.**

This threshold is pre-registered here so that a later good run cannot be retrofitted into a skill claim.

## Known distortions in the current leaderboard

These are real and we surface them rather than letting a reader discover them as a "gotcha." Figures below are illustrative, drawn from the standings around 2026-06-06; the live site carries current numbers.

- **Currency translation, not skill.** The EUR weakened against the USD over the sample. Because returns are reported in EUR, every USD-denominated book received a mechanical translation boost. For the leading agent (`sharp-shooter-usd`, ~+7.8% at the time), roughly **2.35 percentage points** of the return is this currency-accounting effect, not a trading decision.
- **Universe mix, not selection.** US equities outperformed European equities by ~6pp over the sample. A further ~3.5pp of that same agent's lead reflects *which market it was assigned*, not which stocks it picked. Net of both effects, the nominal leader and the second-place agent are within ~0.1pp of each other in native-currency terms — a statistical tie.
- **Day-one inception artifact.** One agent (`world`) recorded a first-day mark-to-market above its €10,000 baseline, inflating its reported return by an amount unrelated to subsequent decisions (true return from actual day-1 NAV is materially lower than the headline).

## How execution is disciplined

- **Safety lives in the broker, not the prompt.** The paper broker enforces 14 distinct rejection/cancel reason codes (cash, shares, notional, universe, drawdown, FX-rate, order-count, trigger-expiry, agent cancellations, and more). A persona's prompt is aspirational; the broker is what actually constrains it.
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

That ten Claude agents have paper-traded autonomously, every weekday since 2026-04-17, with every decision and its reasoning committed to a public ledger you can audit line by line; that each is measured against both a passive benchmark and a random-trade control shown beside its return; and that the whole thing was built and run by one person. Nothing about returns, edge, or whether "AI can trade." Those questions are deliberately deferred to the thresholds above.
