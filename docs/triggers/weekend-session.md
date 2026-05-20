# Weekend Crypto Session — RemoteTrigger Prompt

**Cron:** `0 20 * * 6,0` (Sat-Sun 20:00 UTC)
**Roster:** `satoshi`, `yolo-sapiens-eur`, `yolo-sapiens-usd`
**Cadence-invariant pipeline:** identical helpers as the weekday session, only the roster differs.

This doc is the canonical text to paste into the RemoteTrigger configuration in claude.ai. The weekday trigger lives at `weekday-session.md` and shares 95% of this text — only `ROSTER` and the trading-round addendum differ.

## Architecture — DISPATCH, do NOT impersonate

Every persona-authored output MUST come from a `Task` tool dispatch — never inline-authored by the orchestrator. See `weekday-session.md` for the full rationale (clean per-agent journal-rewrite loop, voice-drift containment).

**Dispatch substrate.** Project-level subagents in `.claude/agents/*.md` are NOT auto-registered as `subagent_type` values by Claude Code. We dispatch via `subagent_type="general-purpose"` with the persona body injected by `engine.persona_dispatch.wrap_persona_prompt`:

```python
from engine.persona_dispatch import wrap_persona_prompt
wrapped, model = wrap_persona_prompt(agent_id, task_prompt)
# Task(subagent_type="general-purpose", model=model, prompt=wrapped)
```

---

## Trigger prompt

```
You are running the Midas weekend crypto trading session for today's date.

Repository: ~/Dev/midas (already cloned).

# Step 0 — Realign sandbox to current origin/main (CRITICAL, before anything else)
git fetch origin main
git reset --hard origin/main
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" || {
    echo "FATAL: HEAD not at origin/main after reset" >&2; exit 1
}
# RemoteTrigger sandbox VMs are reused across fires; the named workspace
# branch (claude/<slug>) carries stale local state from previous fires.
# 2026-05-05 weekday incident: session started from May 3 weekend's tip,
# missed two intervening commits, produced duplicate sells of positions
# that no longer existed in current state. Always realign first.

Activate the venv:
    source .venv/bin/activate

ROSTER = ["satoshi", "yolo-sapiens-eur", "yolo-sapiens-usd"]

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

# Step 1 — Market data (store-only, no network)
python scripts/fetch_market_data.py
# Writes data/market/today.json from the committed OHLCV store. Sandbox
# has no outbound HTTP — that is by design. If this exits non-zero, abort
# and report. Do not improvise a hand-built today.json.

# Step 2 — Trading round (DISPATCH IN PARALLEL — one Task call per agent)
For each agent_id in ROSTER:
    from scripts.daily_session import (
        CONDITIONAL_ORDER_INSTRUCTIONS,
        render_active_triggers_for_agent,
    )
    wrapped, model = wrap_persona_prompt(
        agent_id,
        TRADING_PROMPT.format(
            agent_id=agent_id, today=today, yesterday=yesterday,
            conditional_instructions=CONDITIONAL_ORDER_INSTRUCTIONS,
            active_triggers=render_active_triggers_for_agent(agent_id),
        ),
    )
Dispatch via Task with subagent_type="general-purpose", model=model,
prompt=wrapped. All 3 dispatches MUST be issued in the SAME message so
they run in parallel. Collect agent_results = {agent_id: {"commentary":
..., "trades": [...], "cancels": [...]}} (cancels optional).

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

Weekend session — restrict orders to crypto pairs in your base currency.
Equities/forex markets are closed; the broker would reject those orders
anyway.

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
  ]
}
"""

After all 3 results arrive:
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
For each agent_id in post_prompts (3 agents):
    wrapped, model = wrap_persona_prompt(agent_id, post_prompts[agent_id])
Dispatch via Task with subagent_type="general-purpose", model=model,
prompt=wrapped. All 3 dispatches MUST be issued in the SAME message so
they run in parallel. Parse each response with parse_post_response.
Collect agent_posts = {agent_id: [PostPayload, ...]}.

# Step 7 — Save content (the bundle MUST contain all 10 agents)
    from scripts.daily_session import step_save_content
    # Reuse portfolio_summaries built in Step 4 — do NOT rebuild here.
    step_save_content(
        bundle_date=today,
        market_data=market_payload,
        agent_results=agent_results,             # 3 agents
        agent_posts=agent_posts,                 # 3 agents
        portfolio_summaries=portfolio_summaries, # 10 agents — non-default!
        leaderboard=leaderboard,
        blog_draft=blog_draft,
        oracle_posts=oracle_posts,
    )
After this returns, verify data/output/{today}.json contains 10 agent keys.
If fewer than 10, the bundle is malformed — abort.

# Step 8 — Memory rewrite (DISPATCH IN PARALLEL — 3 agents + the-oracle)
    from scripts.daily_session import (
        step_build_memory_update_prompts, step_save_memories
    )
    memory_prompts = step_build_memory_update_prompts(
        agent_results=agent_results,
        agent_posts=agent_posts,
        portfolio_summaries=portfolio_summaries,
    )
For each agent_id in memory_prompts (3 traders + the-oracle):
    wrapped, model = wrap_persona_prompt(agent_id, memory_prompts[agent_id])
Dispatch via Task with subagent_type="general-purpose", model=model,
prompt=wrapped. All 4 dispatches MUST be issued in the SAME message so
they run in parallel. Each subagent rewrites its own first-person journal
in full and returns the new content as plain markdown.
    new_journals = {agent_id: response_text for ...}
    step_save_memories(new_journals)
After this, data/agent_memory/{satoshi,yolo-sapiens-eur,yolo-sapiens-usd,the-oracle}.md
must show fresh mtimes. If any are unchanged, that agent's dispatch was
skipped — abort.

# Step 9 — Baselines refresh (ALWAYS, no conditional)
    from scripts.daily_session import step_build_baselines
    step_build_baselines()
data/baselines/* must be modified by this call. If git diff shows no
change in data/baselines/, this step was skipped — abort.

# Step 10 — Commit and push
Commit data/ first, with the richer message:
    git add data/
    git commit -m "chore: weekend crypto session {today}"
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
  - data/agent_memory/*.md (running 3 + the-oracle)
  - data/portfolios/*/snapshots.json (all 10)
  - data/posts/{today}.json
  - data/blog/{today}.md
Also confirm the leaderboard in data/output/{today}.json was produced
by step_build_leaderboard, not by hand. Spot-check one EUR agent and
one USD agent against (portfolio_mtm_eur / 10_000 - 1) * 100 — values
should match to the cent. If a row's return_pct equals
(snapshots[-1].portfolio_value / snapshots[0].portfolio_value - 1) * 100
for an agent seeded with non-cash positions, the helper was bypassed —
abort. (2026-05-15 weekday session bug.)
Also confirm: this session issued at least 11 Task tool dispatches
(3 trade + 1 oracle + 3 post + 4 journal), every one with
subagent_type="general-purpose" and a wrap_persona_prompt-built prompt.
If fewer, an agent step was inlined instead of dispatched — abort and re-run.
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

## Diff vs weekday trigger

The weekday trigger uses:
```
ROSTER = [
    "steady-eddie-eur", "steady-eddie-usd",
    "sharp-shooter-eur", "sharp-shooter-usd",
    "yolo-sapiens-eur", "yolo-sapiens-usd",
    "satoshi", "monsieur-forex", "goldfinger", "world",
]
```
And the TRADING_PROMPT does NOT include the "Weekend session — restrict orders to crypto pairs" addendum.
Every other step is byte-identical.
