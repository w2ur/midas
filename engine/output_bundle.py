"""Output bundle — single JSON file per day combining all session data.

Read by the API, the frontend, and any downstream publisher. The single source
of truth for a session's public output.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from engine.blog import BlogDraft
from engine.config import get_config
from engine.posts import PostPayload
from engine.research_note import parse_research_note


def __getattr__(name: str):
    # Lazy ROSTER — resolved at access time so config overrides (tests / CLI) are honoured.
    # Oracle is a narrator and lives under bundle["narrator"], not bundle["agents"].
    if name == "ROSTER":
        return get_config().trading_roster
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_day_number(for_date: date | None = None) -> int:
    """Return the day-number of `for_date` (defaults to today) in the experiment.

    Idempotent on retry: if a bundle for `for_date` already exists, returns its
    ordinal position (1-indexed) among sorted bundle files. Otherwise returns
    `len(existing) + 1`. Prevents the day count from advancing when a session
    is retried after a crash.
    """
    output_dir = get_config().output_dir
    if not output_dir.exists():
        return 1
    existing = sorted(f.stem for f in output_dir.iterdir() if f.suffix == ".json")
    target = (for_date or date.today()).isoformat()
    if target in existing:
        return existing.index(target) + 1
    return len(existing) + 1


def assemble_output_bundle(
    bundle_date: date,
    market_data: dict,
    agent_results: dict[str, dict],
    agent_posts: dict[str, list[PostPayload]],
    portfolio_summaries: dict[str, dict],
    leaderboard: list[dict],
    blog_draft: BlogDraft,
    oracle_posts: list[PostPayload],
) -> dict:
    """Assemble the complete daily output bundle.

    Layout:
        { "date", "market_snapshot", "agents": {id: {commentary, trades, portfolio, posts}},
          "narrator": {"blog_draft", "posts"}, "leaderboard" }

    The agents map always contains the full 10-agent ROSTER, regardless of which
    agents ran this session. Non-running agents get commentary=None, empty
    trades/posts, and their carry-forward portfolio from `portfolio_summaries`.
    This keeps the bundle shape invariant across cadences (weekday/weekend/holiday)
    so the site can always render every dossier.
    """
    agents = {}
    for aid in get_config().trading_roster:
        result = agent_results.get(aid)
        if result is None:
            agents[aid] = {
                "commentary": None,
                "trades": [],
                "research_note": None,
                "portfolio": portfolio_summaries.get(aid, {}),
                "posts": [],
            }
        else:
            note = parse_research_note(result.get("research_note"))
            agents[aid] = {
                "commentary": result.get("commentary", ""),
                "trades": result.get("trades", []),
                "research_note": note.to_dict() if note is not None else None,
                "portfolio": portfolio_summaries.get(aid, {}),
                "posts": [p.to_dict() for p in agent_posts.get(aid, [])],
            }
    return {
        "date": bundle_date.isoformat(),
        "market_snapshot": market_data,
        "agents": agents,
        "narrator": {
            "blog_draft": blog_draft.to_dict(),
            "posts": [p.to_dict() for p in oracle_posts],
        },
        "leaderboard": leaderboard,
    }


def save_output_bundle(bundle_date: date, bundle: dict) -> Path:
    """Save the output bundle to data/output/YYYY-MM-DD.json."""
    output_dir = get_config().output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{bundle_date.isoformat()}.json"
    path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    return path
