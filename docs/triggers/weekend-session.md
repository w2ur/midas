# Weekend Crypto Session — RemoteTrigger Prompt

**Cron:** `0 20 * * 6,0` (Sat-Sun 20:00 UTC)
**Roster:** `satoshi`, `yolo-sapiens-eur`, `yolo-sapiens-usd`
**Cadence-invariant pipeline:** identical helpers as the weekday session, only the roster differs.

This doc is the canonical text to paste into the RemoteTrigger configuration in claude.ai. The weekday trigger lives at `weekday-session.md` and shares 95% of this text — only `ROSTER` and the agent-dispatch instruction text differ.

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
must be invoked.

# Step 1 — Market data (store-only, no network)
python scripts/fetch_market_data.py
# Writes data/market/today.json from the committed OHLCV store. Sandbox
# has no outbound HTTP — that is by design. If this exits non-zero, abort
# and report. Do not improvise a hand-built today.json.

# Step 2 — Author orders for each agent in ROSTER
For each agent in ROSTER, dispatch the agent prompt with this addendum:
"Weekend session — restrict orders to crypto pairs in your base currency.
Equities/forex markets are closed; broker would reject those orders anyway."
Collect agent_results = {agent_id: {"commentary": ..., "trades": [...]}}.
Then call:
    from scripts.daily_session import step_author_orders
    step_author_orders(agent_results)

# Step 3 — Fill orders
    from scripts.daily_session import step_fill_orders
    from engine.portfolio import PortfolioManager
    pm = PortfolioManager(base_dir="data/portfolios")
    fills = step_fill_orders(today, pm)

# Step 4 — Snapshot every portfolio (all 10, not just ROSTER)
    from scripts.daily_session import step_update_snapshots
    market_payload = json.load(open("data/market/today.json"))
    step_update_snapshots(market_payload)

# Step 5 — Build prompts for the post round
    from scripts.daily_session import step_build_post_prompts
    post_prompts = step_build_post_prompts(agent_results)
Dispatch each prompt to its agent; collect agent_posts.

# Step 6 — Build Oracle prompt and dispatch
    from scripts.daily_session import step_build_oracle_prompt, step_load_memories
    oracle_prompt = step_build_oracle_prompt(
        market_data=market_payload,
        agent_results=agent_results,
        agent_posts={aid: [p.to_dict() for p in posts] for aid, posts in agent_posts.items()},
        leaderboard=leaderboard,  # built from snapshots
    )
Dispatch to The Oracle agent; parse response into blog_draft + oracle_posts.

# Step 7 — Save content (the bundle MUST contain all 10 agents)
    from scripts.daily_session import step_save_content, build_portfolio_summaries
    portfolio_summaries = build_portfolio_summaries()  # ALL 10 agents, carry-forward
    step_save_content(
        bundle_date=today,
        market_data=market_payload,
        agent_results=agent_results,           # 3 agents
        agent_posts=agent_posts,               # 3 agents
        portfolio_summaries=portfolio_summaries,  # 10 agents — non-default!
        leaderboard=leaderboard,
        blog_draft=blog_draft,
        oracle_posts=oracle_posts,
    )
After this returns, verify data/output/{today}.json contains 10 agent keys.
If fewer than 10, the bundle is malformed — abort.

# Step 8 — Memory (Ring 2 journal rewrite)
    from scripts.daily_session import (
        step_build_memory_update_prompts, step_save_memories, step_load_memories
    )
    memory_prompts = step_build_memory_update_prompts(
        agent_results=agent_results,
        agent_posts=agent_posts,
        portfolio_summaries=portfolio_summaries,
        ...
    )
Dispatch each to its agent (running 3 + The Oracle). Parse responses.
    step_save_memories(new_journals)
After this, data/agent_memory/{satoshi,yolo-sapiens-eur,yolo-sapiens-usd,the-oracle}.md
must show fresh mtimes. If they don't, Step 8 was skipped — abort.

# Step 9 — Baselines refresh (ALWAYS, no conditional)
    from scripts.daily_session import step_build_baselines
    step_build_baselines()
data/baselines/* must be modified by this call. If git diff shows no
change in data/baselines/, this step was skipped — abort.

# Step 10 — Commit and push
    from scripts.daily_session import step_git_commit_push
    step_git_commit_push(dry_run=False)
Commit message: "chore: weekend crypto session {today}"

# Self-check before reporting success
git show HEAD --stat must include:
  - data/output/{today}.json
  - data/baselines/**
  - data/agent_memory/*.md (running 3 + the-oracle)
  - data/portfolios/*/snapshots.json (all 10)
  - data/posts/{today}.json
  - data/blog/{today}.md
If any of these is missing, the corresponding step was skipped. Do not
report success.
```

---

## Banned phrases

These were the rationalizations that broke the 2026-04-25 session. The new
prompt explicitly forbids them:

- "Baselines already current — last snapshot dated …" → must call `step_build_baselines()`
- "Network blocked. Let me update today.json with today's BTC close" → must call `python scripts/fetch_market_data.py` (already store-only)
- "Now I'll write Oracle's blog + posts as the Oracle persona" → must dispatch to the Oracle agent and call `step_save_content`
- "rewrite the four journals in each agent's voice" inline → must dispatch to each agent and call `step_save_memories`

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
And the agent-dispatch addendum is empty (no crypto-only restriction).

Every other step is byte-identical.
