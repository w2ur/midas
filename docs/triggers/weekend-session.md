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

Repository: ~/Dev/midas (already cloned). Activate the venv:
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
    wrapped, model = wrap_persona_prompt(
        agent_id,
        TRADING_PROMPT.format(agent_id=agent_id, today=today, yesterday=yesterday),
    )
Dispatch via Task with subagent_type="general-purpose", model=model,
prompt=wrapped. All 3 dispatches MUST be issued in the SAME message so
they run in parallel. Collect agent_results = {agent_id: {"commentary":
..., "trades": [...]}}.

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

Output JSON only, no other text:
{
  "commentary": "your day's reasoning, in your voice (3-8 sentences)",
  "trades": [
    {"action": "buy"|"sell", "ticker": "TICKER", "shares": int, "reasoning": "..."},
    ...
  ]
}
"""

After all 3 results arrive:
    from scripts.daily_session import step_author_orders
    step_author_orders(agent_results)

# Step 3 — Fill orders
    from scripts.daily_session import step_fill_orders
    from engine.portfolio import PortfolioManager
    pm = PortfolioManager(base_dir="data/portfolios")
    fills = step_fill_orders(today, pm)

# Step 4 — Snapshot every portfolio (all 10, not just runners)
    from scripts.daily_session import step_update_snapshots
    market_payload = json.load(open("data/market/today.json"))
    step_update_snapshots(market_payload)

# Step 5 — Oracle narrative (DISPATCH — one Task call to the-oracle)
Build leaderboard from snapshots first, then:
    from scripts.daily_session import step_build_oracle_prompt, step_load_memories
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
    from scripts.daily_session import step_save_content, build_portfolio_summaries
    portfolio_summaries = build_portfolio_summaries()  # ALL 10 agents, carry-forward
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
    from scripts.daily_session import step_git_commit_push
    step_git_commit_push(dry_run=False)
Commit message: "chore: weekend crypto session {today}" (the orchestrator
commits data/ itself with this richer message before calling the helper;
the helper sees nothing staged and proceeds to the push).

**Push must land on origin/main, not the sandbox's working branch.** RemoteTrigger
sessions check out a throwaway branch like `claude/<slug>`; a plain `git push`
publishes THAT branch, leaving main (and the public Vercel deploy) untouched.
Either let `step_git_commit_push` handle the push (it uses `git push origin
HEAD:main`), or, if you push manually, use the same explicit refspec — never
a bare `git push`.

# Self-check before reporting success
git show HEAD --stat must include:
  - data/output/{today}.json
  - data/baselines/**
  - data/agent_memory/*.md (running 3 + the-oracle)
  - data/portfolios/*/snapshots.json (all 10)
  - data/posts/{today}.json
  - data/blog/{today}.md
Also confirm: this session issued at least 11 Task tool dispatches
(3 trade + 1 oracle + 3 post + 4 journal), every one with
subagent_type="general-purpose" and a wrap_persona_prompt-built prompt.
If fewer, an agent step was inlined instead of dispatched — abort and re-run.
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
- "Network blocked. Let me update today.json with today's BTC close" → MUST call `python scripts/fetch_market_data.py` (already store-only)

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
