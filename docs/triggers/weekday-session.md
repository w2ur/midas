# Weekday Session — RemoteTrigger Prompt

**Cron:** `0 20 * * 1-5` (Mon-Fri 20:00 UTC)
**Roster:** 10 trading agents + The Oracle.
**Cadence-invariant pipeline:** identical helpers as the weekend session, only the roster differs.

This doc is the canonical text to paste into the RemoteTrigger configuration in claude.ai. It is the only trigger prompt in the repo: the weekend RemoteTrigger was retired on 2026-05-23 and replaced by `refresh-leaderboard.yml`, a valuation-only GitHub Action that runs no agents and needs no prompt.

## Architecture — DISPATCH, do NOT impersonate

Every persona-authored output (commentary, trades, posts, blog, journal rewrites) MUST come from a `Task` tool dispatch — never inline-authored by the orchestrator session. This keeps each persona's context window isolated; the journal-rewrite loop in particular is contaminated if one orchestrator just spent the last hour also being the other 9 agents.

**Dispatch substrate.** Project-level subagents in `.claude/agents/*.md` are NOT auto-registered as `subagent_type` values by Claude Code (neither locally nor in cloud sessions — the Apr 29 session aborted on this). We dispatch via `subagent_type="general-purpose"` with the persona body injected by `engine.persona_dispatch.wrap_persona_prompt`. The orchestrator never improvises persona content; it loads the file and forwards.

```python
from engine.persona_dispatch import wrap_persona_prompt

wrapped, model = wrap_persona_prompt(agent_id, task_prompt)
# Then dispatch:
#   Task(subagent_type="general-purpose", model=model, prompt=wrapped)
```

`model` is `"opus"` for every current persona — pass it through so the dispatch matches the frontmatter intent. If `model` is `None`, omit the parameter and let the harness pick.

---

## Running on Claude Opus 5

The orchestrator runs on **Claude Opus 5** (set in the RemoteTrigger `job_config`, outside this repo). The aliases in `.claude/agents/*.md` frontmatter resolve to current releases, so **the 10 traders and the Manager are on Opus 5 as well**; only the Oracle (`model: sonnet`) is not.

Opus 5 runs this prompt well as written. The deltas below are the behaviours that needed tuning, from Anthropic's [Opus 5 prompting guidance](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5).

**Delegation is the one that actually matters here.** Opus 5 reaches for subagents more readily than Opus 4.8 did, and this session is built almost entirely out of subagent dispatches — so the failure mode isn't hypothetical. The dispatch count is a fixed contract, not a budget to fill: every extra dispatch is a full agent round of cost and latency producing output that nothing downstream consumes.

The self-check used to read *"at least 31 dispatches"*. That is exactly the wrong shape for this model — a floor invites padding — and it was also **wrong on the arithmetic**: it counted 10 trade + 1 oracle + 10 post + 10 journal, omitting the Manager dispatch (Step 4b) and the Oracle's own journal rewrite (Step 8 dispatches 11, not 10). The real total is **33**, and it is now asserted as an exact count.

**Verification scaffolding is deliberately absent.** Opus 5 checks its own work unprompted; instructing it to verify compounds with that and burns a round for no signal. What remains are *contract assertions* — "`git show HEAD --stat` must include X", "the bundle must contain 10 agent keys" — deterministic checks against committed artifacts, not self-review. **Do not delete those, and do not add "verify with a subagent" steps back.** The distinction is the whole point: assertions on artifacts stay, requests to re-think go.

**Scope.** Opus 5 will widen a task it judges under-specified. An unattended trading session must not: a helper it decides to "fix" mid-run mutates the ledger, and the ledger is the product. The prompt now says deliver this pipeline at this scope, raise concerns in a sentence, and keep going.

**Deliverable length needed no change — verified, not assumed.** Opus 5 writes longer files than prior models, and the journals are agent-authored markdown rewritten in full every session, so they were the obvious exposure. `engine/agent_memory.build_memory_update_prompt` already caps them at a hard 250-token ceiling with an explicit "prune ruthlessly, no padding" instruction. Worth keeping that way: `journal_excerpt` keeps the **tail**, so a journal that grew past 4 000 chars would silently drop its oldest beliefs out of every prompt that reads it, with nothing failing.

**Not applicable to this trigger:** the thinking-disabled artifacts (a tool call written as plain text so the call never runs; leaked `<thinking>` tags). Thinking is on by default on Opus 5 and nothing here disables it. If an effort setting is ever added to the routine, note that disabling thinking is rejected above `high` effort — and that lowering effort shortens *thinking*, not visible output.

---

## Environment setup — pre-bake the venv (configured OUTSIDE this repo)

The venv must already exist when the session starts. Building it inside the
session is what cost 63 hours on 2026-07-31: the run stalled ~5 minutes in,
mid-install, and did not resume until 08-02.

This half of the fix cannot live in git — it belongs to the RemoteTrigger
environment configuration on claude.ai, which the sandbox applies at image-build
time, before any session is timed. Set its setup step to:

```bash
bash scripts/bootstrap_venv.sh
```

That is the full command. The script is idempotent: on a warm image where the
venv already matches `requirements.txt` it prints `venv already current` and
exits immediately, so it is safe to leave in the setup step permanently. It
rebuilds only when the lockfile hash or the interpreter version changes — which
is exactly when a rebuild is warranted.

The session side is the matching `--check` in Step 0 below. The two are a pair:
build out here where nothing is timed, assert in there where everything is.

**Do not "fix" a failing `--check` by adding a build to the trigger prompt.**
That restores the 2026-07-31 failure mode in full. Repair the image instead.

---

## Trigger prompt

```
You are running the Midas weekday trading session for today's date.

Repository: already cloned — the checkout is at /home/user/midas in the cloud
sandbox (verified 2026-08-02). Work from the repo root; don't assume a path,
`git rev-parse --show-toplevel` is authoritative.

# Step 0 — Realign sandbox to current origin/main (CRITICAL, before anything else)
git fetch origin main
git reset --hard origin/main
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" || {
    echo "FATAL: HEAD not at origin/main after reset" >&2; exit 1
}
# RemoteTrigger sandbox VMs are reused across fires; the named workspace
# branch (claude/<slug>) carries stale local state from previous fires.
# 2026-05-05 incident: the weekday session started from May 3 weekend's
# tip, missed two intervening commits (May 4 session + OHLCV), produced
# duplicate sells of positions that no longer existed in current state.
# Push was rejected by the session-integrity guard. Always realign first.

Activate the venv, then VERIFY it — do NOT build or repair it here:
    source .venv/bin/activate
    bash scripts/bootstrap_venv.sh --check
# The venv is built at IMAGE-BUILD time by the cloud environment's setup
# script, not in the session. --check is network-free and returns in ms: it
# confirms Python >= 3.12 and that the venv matches the current
# requirements.txt, then gets out of the way.
# 2026-07-31 incident: the session stalled ~5 min in WHILE REBUILDING THE VENV
# and resumed 63 hours later. The install of pandas/bt/pandas-ta sat in the
# timed critical path of every run, and that is precisely where the run died.
# If --check fails: ABORT the session and report. Do not pip install, do not
# rebuild, do not "just try once more" — a rebuild here is the failure mode.
# Aborting is cheap: nothing commits, session-watchdog files an issue the next
# morning, and the next scheduled session starts clean on a repaired image.

# Step 0c — Anchor the session clock + ledger base (CRITICAL, after realign)
    from datetime import date
    from scripts.session_guard import anchor_session
    anchor_session(today)
# Pins `today`, origin/main's SHA, and the wall-clock start. step_author_all
# and step_git_commit_push re-validate all three and abort the run if the
# sandbox stalled, the date rolled over, or the ledger moved on main.
# 2026-07-31 incident: the sandbox fired on time, stalled ~5 min in during
# the venv rebuild, and resumed ~63 HOURS later on 08-02. Step 0 had passed
# legitimately (main really was at 02949e3 at 20:04 on 07-31), so nothing
# caught it; the session authored a full set of 07-31 artifacts against a
# dead snapshot. main meanwhile had the 07-31 OHLCV, the 08-01 trigger fires
# and the 08-01 weekend refresh. The push was rejected and auto-merge failed
# on conflicts — merging would have reverted the 08-01 fills.
# If the guard aborts: do NOT reconcile by hand. Abandon the run and report;
# the next scheduled session starts clean.

ROSTER = [
    "steady-eddie-eur", "steady-eddie-usd",
    "sharp-shooter-eur", "sharp-shooter-usd",
    "yolo-sapiens-eur", "yolo-sapiens-usd",
    "satoshi", "monsieur-forex", "goldfinger", "world",
]

Drive every step through the helpers in scripts/daily_session.py.
Do NOT write files inline. Do NOT skip steps with rationalizations like
"already current" or "would be unchanged" — every step is idempotent and
must be invoked. Do NOT write commentary, trades, posts, blog, or
journal content yourself for any agent — every persona-authored output
MUST come back from a Task tool dispatch.

Persona dispatch pattern (used in every step that targets an agent):
    from engine.persona_dispatch import wrap_persona_prompt
    wrapped, model = wrap_persona_prompt(agent_id, task_prompt)
    # dispatch: Task(subagent_type="general-purpose", model=model, prompt=wrapped)

NEVER use subagent_type=<agent_id> directly — project agents are not
registered in the Task registry. Use "general-purpose" + wrapper.

# Operating rules — read before Step 1

DISPATCH BUDGET IS EXACT: 33 Task calls, no more, no fewer.
    10  Step 2   trading round (one per roster agent)
     1  Step 4b  the-manager
     1  Step 5   the-oracle
    10  Step 6   post round (one per roster agent)
    11  Step 8   journal rewrite (10 traders + the-oracle)
Do NOT spawn a subagent for anything else: not to read a file, not to
check a number, not to review or verify your own work, not to
investigate a helper that returned something surprising, not to
"parallelise" a step this prompt does not already parallelise. Every
action outside those five dispatch points is a direct helper call you
make yourself. A 34th dispatch means you invented work — stop and
report it instead of running it.

DELIVER THIS PIPELINE AT THIS SCOPE. Do not add steps, do not fix
unrelated things you notice in the repo, do not refactor a helper, do
not improve an artifact that is merely not to your taste. If a step
looks wrong or a helper looks buggy, say so in one sentence in your
final report and keep running the pipeline as written — do NOT quietly
repair it mid-session. The abort conditions named in the steps below
are the exception, and they mean STOP, not FIX.

NEVER reconcile state by hand. Portfolios, orders, baselines and the
leaderboard are written by helpers only. If they disagree with each
other, that is a finding to report, not a file to edit.

The checks in this prompt are contract assertions on committed
artifacts, not requests to double-check your reasoning. Run them
exactly as written, and add none of your own. You already verify your
work without being told; an extra pass here costs a full agent round
and tells you nothing new.

Keep your own narration short — a line per step is plenty, and the
transcript is read only when something breaks. This applies to YOUR
output only. Persona text comes back from dispatches and is never
yours to shorten, rewrite, or tidy.

# Step 1 — Market data (store-only, no network)
python scripts/fetch_market_data.py
# Writes data/market/today.json from the committed OHLCV store. Sandbox
# has no outbound HTTP — that is by design. If this exits non-zero, abort
# and report. Do not improvise a hand-built today.json.

# Step 2 — Trading round (DISPATCH IN PARALLEL — one Task call per agent)
    from scripts.daily_session import (
        CONDITIONAL_ORDER_INSTRUCTIONS,
        render_active_triggers_for_agent,
    )
For each agent_id in ROSTER:
    wrapped, model = wrap_persona_prompt(
        agent_id,
        TRADING_PROMPT.format(
            agent_id=agent_id, today=today, yesterday=yesterday,
            conditional_instructions=CONDITIONAL_ORDER_INSTRUCTIONS,
            active_triggers=render_active_triggers_for_agent(agent_id),
        ),
    )
Dispatch via Task with subagent_type="general-purpose", model=model,
prompt=wrapped. All 10 dispatches MUST be issued in the SAME message so
they run in parallel. Collect agent_results = {agent_id: {"commentary":
..., "trades": [...], "cancels": [...], "research_note": {...}}}
(cancels optional). PRESERVE the FULL response dict per agent — in
particular `research_note` is load-bearing: it is the ONLY input to the
analysts+Manager pipeline (Step 4a/4b) and the public bundle. Dropping it
does NOT crash anything — the Manager silently runs on zero signal and
writes empty HOLD reviews while looking healthy. Keep every key the agent emits.

TRADING_PROMPT (the task body — wrap_persona_prompt prepends the persona):
"""
It is session day {today}. You are trading independently — you do NOT
see what other agents are doing today. React to the market and your own
prior history.

Read your context from disk:
- data/portfolios/{agent_id}/portfolio.json    (cash + positions, in your base currency)
- data/portfolios/{agent_id}/trades.json       (your trade history; tail the last 50 lines)
- data/agent_memory/{agent_id}.md              (your prior-self journal — your beliefs, lessons, biases)
- data/market/today.json                       (today's market snapshot + benchmarks)
- data/blog/{yesterday}.md                     (yesterday's overall session, narrated by The Oracle — read for continuity, optional if missing)

Stay in your persona, mandate, universe, and base currency. Long-only;
use bearish ETFs to express short views. Respect your position limits
and safety rails — the broker will reject violations anyway.

{conditional_instructions}

{active_triggers}

Output JSON only, no other text:
{
  "commentary": "your day's reasoning, in your voice (3-8 sentences)",
  "trades": [
    {"action": "buy"|"sell", "ticker": "TICKER", "shares": int, "reasoning": "...",
     "trigger": {"op": ">="|"<=", "level": <number>}, "expires": "YYYY-MM-DD"}
    // trigger + expires are OPTIONAL; omit for an immediate market order.
  ],
  "cancels": [
    {"target_order_id": "ord_...", "reasoning": "..."}
    // OPTIONAL; only include if you want to remove a pending conditional from a prior session.
  ],
  "research_note": {
    "thesis": "1-2 sentence actionable view (<=280 chars)",
    "conviction": 0,            // integer 0-10
    "tickers": ["TICKER", ...], // instruments the thesis is about
    "action_bias": "strong_buy"|"buy"|"hold"|"reduce"|"exit",
    "horizon": "days"|"weeks"|"months",
    "catalysts": "what would confirm/break the thesis (<=200 chars)",
    "currency": "EUR"|"USD"     // the instruments' denomination
  }
  // research_note carries your VIEW (not sizing) for the Manager desk.
  // ALWAYS include it. See your persona file for details.
}
"""

After all 10 results arrive:
    from scripts.daily_session import step_author_all
    step_author_all(agent_results, today)
    # ^ MUST be the helper. Do NOT loop in prose calling step_author_orders
    # per agent — that's the 2026-05-15 leaderboard-bug pattern (per-agent
    # loops as natural-language instructions improvise away). The helper
    # iterates over agent_results, looks up each agent's base currency from
    # disk, and writes trades to the outbox and cancels to data/orders/cancels/.

# Step 3 — Fill orders
    from scripts.daily_session import step_fill_orders
    from engine.portfolio import PortfolioManager
    pm = PortfolioManager(base_dir="data/portfolios")
    fills = step_fill_orders(today, pm)

# Step 4 — Snapshot every portfolio (all 10, not just runners)
    from scripts.daily_session import step_update_snapshots, build_portfolio_summaries
    market_payload = json.load(open("data/market/today.json"))
    step_update_snapshots(market_payload)
    # Build portfolio_summaries ONCE, here, immediately after snapshots.
    # Reused by Step 5 (leaderboard), Step 7 (save_content), Step 8
    # (memory rewrite). Single source of truth — do NOT recompute later.
    portfolio_summaries = build_portfolio_summaries()  # ALL 10 agents, carry-forward

# Step 4a — Manager pipeline (PAPER, PRIVATE — the analysts+Manager pivot)
# The 10 agents are RESEARCH ANALYSTS: their research_note fields feed a
# deterministic baseline-manager and an LLM Manager that runs a small private
# paper book (the-manager) on its OWN channel. NONE of this is public:
#   - the-manager / baseline-manager are NOT in the roster, leaderboard,
#     bundle, posts, journals, or site (verified: not in AGENT_POST_TIMES).
#   - The Oracle (Step 5), posts (Step 6), and the bundle (Step 7) receive
#     ZERO manager data. Do NOT pass manager artifacts to any of them.
#   - Manager fills land in data/orders/manager-inbox/ — NEVER the public inbox.
# This runs AFTER fills+snapshots (so portfolios are current) and BEFORE the
# Oracle (so the Oracle never sees it). All steps are non-LLM EXCEPT 4b's dispatch.
    from scripts.daily_session import (
        step_resolve_manager_outcomes,
        step_build_baseline_manager,
        step_build_manager_prompt,
        step_apply_manager_decision,
    )
    # 4a-i — Resolve matured past Manager decisions into numeric outcome memory
    #        (return + alpha vs MSCI from the store). MUST run before the prompt
    #        build so the Manager sees fresh memory. Non-LLM.
    step_resolve_manager_outcomes(today)
    # 4a-ii — Deterministic baseline-manager rebalance (the Gate C benchmark the
    #         LLM Manager must beat). Reads the agents' research notes from
    #         agent_results; rebalances only on the 1st weekday of the month. Non-LLM.
    step_build_baseline_manager(agent_results, today)

# Step 4b — LLM Manager (DISPATCH — one Task call to the-manager, opus)
    manager_prompt = step_build_manager_prompt(agent_results, today)
    wrapped, model = wrap_persona_prompt("the-manager", manager_prompt)
    # ^ model resolves to "opus" (the-manager.md frontmatter) — the only
    #   real-money-bound author; stakes justify the tier. Pass it through.
Dispatch via Task with subagent_type="general-purpose", model=model,
prompt=wrapped. Capture the dispatch result into `response_text`. The
response is a single JSON object (ManagerDecision).
    # 4b-apply — Parse (conviction gate enforced in code), write the
    #            manager-review audit artifact (EVERY day, even a HOLD),
    #            author non-HOLD orders to the manager channel, fill the
    #            the-manager book with fees.
    # IMPORTANT: step_apply_manager_decision takes a dict, NOT the raw text —
    # json.loads the response first. A malformed/non-JSON response must
    # degrade to None (→ placeholder review, no orders), never crash:
    import json
    try:
        raw_decision = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        raw_decision = None
    step_apply_manager_decision(raw_decision, today)
    # A HOLD / low-conviction / unparseable day is the EXPECTED normal: it
    # still writes manager-review (audit), authors no orders. Do not treat an
    # empty manager-outbox as an error.

# Step 5 — Oracle narrative (DISPATCH — one Task call to the-oracle)
    from scripts.daily_session import (
        step_build_leaderboard, step_build_oracle_prompt, step_load_memories,
    )
    leaderboard = step_build_leaderboard(portfolio_summaries, on=today)
    # ^ MUST be the helper. Do NOT hand-roll the leaderboard from
    # snapshots.json — the first persisted snapshot is NOT inception for
    # agents seeded with non-cash positions (Monsieur Forex starts with
    # FX cash legs, World with multi-currency baskets). Using
    # snapshots[0]['portfolio_value'] as the baseline understates their
    # returns. The 2026-05-15 weekday session shipped exactly that bug
    # because this step was prose ("build leaderboard from snapshots
    # first") instead of a named helper call. The helper anchors to the
    # €10,000 inception baseline that daily_log.py and baselines.py use
    # everywhere else.
    #
    # After this returns: leaderboard must have 10 entries, and
    # leaderboard[0]['return_pct'] should differ from the previous day's
    # #1 by at most ~3pp on a no-trades day. If the swing is larger and
    # no agent traded, the inception baseline is wrong — abort.
    memories = step_load_memories(ROSTER)
    oracle_prompt = step_build_oracle_prompt(
        market_data=market_payload,
        agent_results=agent_results,
        agent_posts=None,        # posts haven't happened yet — Oracle runs first
        leaderboard=leaderboard,
        agent_memories=memories,
    )
    wrapped, model = wrap_persona_prompt("the-oracle", oracle_prompt)
    # NOTE: model resolves to "sonnet" by design — the-oracle.md's
    # frontmatter declares sonnet because Opus first-token latency on
    # the narrative+10-agent prompt repeatedly tripped the cloud streaming
    # idle timeout (Apr 29). Do NOT manually override to "opus" — see
    # CLAUDE.md "Persona dispatch substrate" section.
Dispatch via Task with subagent_type="general-purpose", model=model,
prompt=wrapped. Parse the response with parse_oracle_response →
blog_draft, oracle_posts.

# Step 6 — Post round (DISPATCH IN PARALLEL — one Task call per agent)
    from scripts.daily_session import step_build_post_prompts
    post_prompts = step_build_post_prompts(
        agent_results,
        oracle_blog=blog_draft.body_md,   # agents react to Oracle's framing too
    )
For each agent_id in post_prompts (10 agents):
    wrapped, model = wrap_persona_prompt(agent_id, post_prompts[agent_id])
Dispatch via Task with subagent_type="general-purpose", model=model,
prompt=wrapped. All 10 dispatches MUST be issued in the SAME message so
they run in parallel. Parse each response with parse_post_response.
Collect agent_posts = {agent_id: [PostPayload, ...]}.

# Step 7 — Save content (the bundle MUST contain all 10 agents)
    from scripts.daily_session import step_save_content
    # Reuse portfolio_summaries built in Step 4 — do NOT rebuild here.
    step_save_content(
        bundle_date=today,
        market_data=market_payload,
        agent_results=agent_results,             # 10 agents
        agent_posts=agent_posts,                 # 10 agents
        portfolio_summaries=portfolio_summaries, # 10 agents
        leaderboard=leaderboard,
        blog_draft=blog_draft,
        oracle_posts=oracle_posts,
    )
After this returns, verify data/output/{today}.json contains 10 agent keys.
If fewer than 10, the bundle is malformed — abort.

# Step 8 — Memory rewrite (DISPATCH IN PARALLEL — 10 agents + the-oracle)
    from scripts.daily_session import (
        step_build_memory_update_prompts, step_save_memories
    )
    memory_prompts = step_build_memory_update_prompts(
        agent_results=agent_results,
        agent_posts=agent_posts,
        portfolio_summaries=portfolio_summaries,
    )
For each agent_id in memory_prompts (10 traders + the-oracle):
    wrapped, model = wrap_persona_prompt(agent_id, memory_prompts[agent_id])
Dispatch via Task with subagent_type="general-purpose", model=model,
prompt=wrapped. All 11 dispatches MUST be issued in the SAME message so
they run in parallel. Each subagent rewrites its own first-person journal
in full and returns the new content as plain markdown.
    new_journals = {agent_id: response_text for ...}
    step_save_memories(new_journals)
After this, every data/agent_memory/*.md (11 files) must show fresh mtimes.
If any are unchanged, that agent's dispatch was skipped — abort.

# Step 9 — Baselines refresh (ALWAYS, no conditional)
    from scripts.daily_session import step_build_baselines
    step_build_baselines()
data/baselines/* must be modified by this call. If git diff shows no
change in data/baselines/, this step was skipped — abort.

# Step 9a — After-tax shadow ledger (ALWAYS, after baselines)
    from scripts.daily_session import step_build_tax_shadow
    step_build_tax_shadow()
# Reporting only — recomputes data/tax_shadow/{agent}.json (PFU 30% drag
# estimate per agent). Never mutates portfolios. Cheap; safe to always run.

# Step 9b — Live leaderboard artifact (ALWAYS)
    from scripts.daily_session import step_write_current_leaderboard
    step_write_current_leaderboard(
        rows=leaderboard,
        trigger=f"session-{today.isoformat()}",
    )
data/leaderboard/current.json must be (re)written by this call. The site's
homepage live-leaderboard widget reads this file; per-day archive pages
keep reading data/output/{today}.json. Reuses the `leaderboard` variable
computed in Step 5 — do NOT recompute.

# Step 10 — Commit and push
Commit data/ first, with the richer message:
    git add data/
    git commit -m "chore: weekday session {today}"
Then ALWAYS hand the push off to the helper:
    from scripts.daily_session import step_git_commit_push
    step_git_commit_push(dry_run=False)
The helper sees nothing staged (you already committed) and pushes HEAD
to origin/main with an explicit refspec.

**DO NOT run `git push` yourself.** RemoteTrigger sessions check out a
throwaway branch like `claude/<slug>`; a bare `git push` publishes THAT
branch, leaving main (and the public Vercel deploy) untouched — exactly
what happened on 2026-04-30. The helper is the only sanctioned push path:
it tries `git push origin HEAD:main` first (fast-forward only) so the
daily snapshot lands on the public deploy regardless of which sandbox
branch you're on.

**If the harness 403s the main push** (2026-05-08 incident — the proxy
started rejecting `HEAD:main` from cloud sandboxes), the helper falls
back to `git push origin HEAD`, putting the commit on the sandbox
branch. The `auto-merge-session.yml` GitHub Action then verifies the
session-integrity rules and merges to main automatically. The helper
prints which path it took — read its output before claiming success.

# Self-check before reporting success
git show HEAD --stat must include:
  - data/output/{today}.json
  - data/baselines/**
  - data/agent_memory/*.md (all 11 — 10 traders + the-oracle)
  - data/portfolios/*/snapshots.json (all 10)
  - data/posts/{today}.json
  - data/blog/{today}.md
  - data/leaderboard/current.json
Also confirm the leaderboard in data/output/{today}.json was produced
by step_build_leaderboard, not by hand. Spot-check one EUR agent and
one USD agent against (portfolio_mtm_eur / 10_000 - 1) * 100 — values
should match to the cent. If a row's return_pct equals
(snapshots[-1].portfolio_value / snapshots[0].portfolio_value - 1) * 100
for an agent seeded with non-cash positions, the helper was bypassed —
abort. (2026-05-15 weekday session bug.)
Also confirm: this session issued EXACTLY 33 Task tool dispatches
(10 trade + 1 manager + 1 oracle + 10 post + 11 journal), every one
with subagent_type="general-purpose" and a wrap_persona_prompt-built
prompt. FEWER means an agent step was inlined instead of dispatched —
abort and re-run. MORE means a dispatch was spawned that this prompt
does not authorise: report the count and what the extra ones did, and
do not treat the run as clean. This is an equality check on purpose —
it read "at least 31" until 2026-08-02, which both undercounted (it
omitted the Manager and the Oracle's journal) and licensed unbounded
extra delegation.
Also confirm the session commit reached origin/main:
    git fetch origin main
    git rev-parse HEAD == git rev-parse origin/main
If origin/main does not point at HEAD, either (a) the helper took the
fallback path and pushed the sandbox branch — confirm by checking the
helper's stdout for "Pushed to sandbox branch '...'", in which case the
auto-merge-session workflow will land it on main within a minute; or
(b) the push failed entirely — re-run `step_git_commit_push`.
If any of these is missing, the corresponding step was skipped. Do not
report success.
```

---

## Banned phrases

These rationalizations have broken past sessions. The new prompt
explicitly forbids them:

- "I'll author Satoshi's trades inline since dispatch is overhead" → MUST dispatch via general-purpose + wrap_persona_prompt("satoshi", ...)
- "Generating commentary for [agent] in this context" → MUST dispatch
- "Now I'll rewrite the journals in each agent's voice" inline → MUST dispatch each journal-rewrite to its agent
- "Now I'll write Oracle's blog + posts as the Oracle persona" → MUST dispatch with wrap_persona_prompt("the-oracle", ...)
- "subagent_type='satoshi' returned 'Agent type not found' so I'll write the trades myself" → MUST switch to subagent_type="general-purpose" with wrap_persona_prompt; never inline
- "Baselines already current — last snapshot dated …" → MUST call `step_build_baselines()`
- "Building the leaderboard from snapshots.json in-place — I'll just diff first/last portfolio_value" → MUST call `step_build_leaderboard(portfolio_summaries, on=today)`. The first persisted snapshot is NOT inception for agents seeded with non-cash positions (Monsieur Forex, World). The helper anchors to €10k inception via `portfolio_mtm_eur`, matching `daily_log.py` and `baselines.py`. Hand-rolling here understated Monsieur Forex / World returns on 2026-05-15.
- "Network blocked. Let me update today.json with today's BTC close" → MUST call `python scripts/fetch_market_data.py` (already store-only)
- "Now I'll `git push` the session commit" → MUST call `step_git_commit_push(dry_run=False)`. A bare `git push` publishes the sandbox's throwaway branch (`claude/<slug>`) instead of advancing main — Apr 30 incident. The helper does the right thing: `HEAD:main` first, fallback to `HEAD` (sandbox branch) if the harness 403s the main push (2026-05-08 incident); the auto-merge-session workflow takes the fallback the rest of the way. Don't second-guess the helper.

## Weekends

There is no weekend trigger. The weekend RemoteTrigger (a 3-agent crypto
roster) was retired on 2026-05-23 and its doc deleted on 2026-08-02;
`refresh-leaderboard.yml` covers Sat/Sun as a valuation-only GitHub Action —
no agents, no Oracle, no journals, no posts. Crypto agents keep weekend
exposure through Friday-authored conditional orders that the trigger watcher
fires. See CLAUDE.md → Session Cadence.
