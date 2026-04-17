"""Blog draft generation — The Oracle's prompt builder, parser, and saver."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from engine.posts import AGENT_DISPLAY_NAMES, PostPayload

BLOG_DIR = Path(__file__).parent.parent / "data" / "blog"


@dataclass
class BlogDraft:
    """Daily blog post draft produced by The Oracle."""

    title: str
    body_md: str
    slug: str

    def to_dict(self) -> dict:
        return {"title": self.title, "body_md": self.body_md, "slug": self.slug}

    @classmethod
    def from_dict(cls, d: dict) -> "BlogDraft":
        return cls(title=d["title"], body_md=d["body_md"], slug=d["slug"])


def build_oracle_prompt(
    day_number: int,
    market_data: dict,
    agent_results: dict[str, dict],
    agent_posts: dict[str, list[dict]],
    leaderboard: list[dict],
) -> str:
    """Build The Oracle's daily prompt — blog draft + narrator posts."""
    market = "\n".join(
        f"  {k}: {v:,.2f}" for k, v in market_data.items() if isinstance(v, (int, float))
    )

    agents_s = ""
    for aid, res in agent_results.items():
        name = AGENT_DISPLAY_NAMES.get(aid, aid)
        agents_s += f"\n  {name}:\n    Commentary: {res.get('commentary', '')}\n"
        for t in res.get("trades", []):
            agents_s += f"    - {t['action']} {t.get('shares', '')} {t['ticker']}: {t.get('reasoning', '')}\n"

    posts_s = ""
    for aid, posts in agent_posts.items():
        name = AGENT_DISPLAY_NAMES.get(aid, aid)
        posts_s += f"\n  {name}:\n"
        for p in posts:
            text = p.get("text", "") if isinstance(p, dict) else str(p)
            posts_s += f'    - "{text}"\n'

    lb_s = "\n".join(
        f"  #{e['rank']} {AGENT_DISPLAY_NAMES.get(e['agent'], e['agent'])}: {e['return_pct']:+.1f}% (EUR)"
        for e in leaderboard
    )

    return f"""You are The Oracle, narrator of the Midas experiment. Day {day_number}.

MARKET DATA TODAY:
{market}

AGENT ACTIVITY TODAY:{agents_s}

AGENT POSTS TODAY:{posts_s}

CURRENT LEADERBOARD (EUR-normalized):
{lb_s}

INSTRUCTIONS: produce a daily blog draft and 1-3 narrator posts following your agent definition.

OUTPUT FORMAT — JSON object, no other text:
{{
  "blog_draft": {{"title": "Day {day_number}: ...", "body_md": "...", "slug": "day-{day_number}-..."}},
  "posts": [{{"text": "...", "mentions": ["agent-id"], "kind": "scoreboard|recap|highlight"}}]
}}
"""


def parse_oracle_response(response: str) -> tuple[BlogDraft, list[PostPayload]]:
    """Parse The Oracle's JSON response (handles code-fenced input)."""
    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1
        end = len(lines) - 1 if lines[-1].strip().startswith("```") else len(lines)
        text = "\n".join(lines[start:end]).strip()
    data = json.loads(text)
    draft = BlogDraft.from_dict(data["blog_draft"])
    posts = [PostPayload.from_agent_output("the-oracle", p) for p in data.get("posts", [])]
    return draft, posts


def save_daily_blog_draft(d: date, draft: BlogDraft) -> Path:
    """Save a blog draft as markdown with YAML frontmatter. Title is always quoted."""
    BLOG_DIR.mkdir(parents=True, exist_ok=True)
    path = BLOG_DIR / f"{d.isoformat()}.md"
    frontmatter = (
        "---\n"
        f'title: "{draft.title}"\n'
        f"slug: {draft.slug}\n"
        f"date: {d.isoformat()}\n"
        "---\n\n"
    )
    path.write_text(frontmatter + draft.body_md, encoding="utf-8")
    return path
