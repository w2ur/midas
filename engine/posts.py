"""Post generation — single source of truth for agent display names and schedule.

Imported by engine/daily_log.py; engine/blog.py and engine/output_bundle.py will import from here in later tasks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from engine.config import get_config

AGENT_DISPLAY_NAMES: dict[str, str] = {
    "steady-eddie-eur": "Steady Eddie EUR",
    "steady-eddie-usd": "Steady Eddie USD",
    "sharp-shooter-eur": "Sharp Shooter EUR",
    "sharp-shooter-usd": "Sharp Shooter USD",
    "yolo-sapiens-eur": "YOLO Sapiens EUR",
    "yolo-sapiens-usd": "YOLO Sapiens USD",
    "satoshi": "Satoshi",
    "monsieur-forex": "Monsieur Forex",
    "goldfinger": "Goldfinger",
    "world": "World",
    "the-oracle": "The Oracle",
}

# Trading agents only — Oracle uses per-kind custom times set by its prompt.
AGENT_POST_TIMES: dict[str, str] = {
    "monsieur-forex": "07:00",
    "steady-eddie-eur": "08:00",
    "steady-eddie-usd": "08:15",
    "sharp-shooter-eur": "09:35",
    "sharp-shooter-usd": "09:45",
    "world": "10:00",
    "goldfinger": "11:00",
    "yolo-sapiens-eur": "random",
    "yolo-sapiens-usd": "random",
    "satoshi": "23:00",
}

AGENT_VOICE: dict[str, str] = {
    "steady-eddie-eur": "Patient quality-focused EU manager. Dry, methodical. Thinks in quarters, not days.",
    "steady-eddie-usd": "Patient quality-focused US manager. Dry, methodical. Thinks in quarters, not days.",
    "sharp-shooter-eur": "Decisive EU momentum trader. UCITS-bounded. No sentiment.",
    "sharp-shooter-usd": "Decisive US momentum trader. Rides trends hard, cuts fast.",
    "yolo-sapiens-eur": "EU cross-asset degen. Self-aware, audacious. 'Cash doesn't double.'",
    "yolo-sapiens-usd": "US cross-asset degen. Self-aware, audacious. Leveraged ETFs are his religion.",
    "satoshi": "On-chain nerd. Halving cycles, F&G index, cites metrics.",
    "monsieur-forex": "Central-bank whisperer. Precise, dispassionate. Macro flows.",
    "goldfinger": "Contrarian commodities veteran. Patient. Oldest asset class.",
    "world": "Global multi-asset manager. Currency-aware. Speaks in EUR-equivalent terms.",
    "the-oracle": "Curious, witty narrator. Sports-commentator energy. Amused by the agents' egos.",
}


def resolved_post_time(agent_id: str, post_date: date) -> str:
    """Resolve AGENT_POST_TIMES[agent_id] — fixed returns verbatim, 'random' returns deterministic HH:MM.

    'random' is seeded by MD5(date_iso::agent_id), truncated to a minute-offset
    within the 09:00-22:00 Paris window. Same (date, agent) always returns the
    same time — needed for SSG-friendly feed rendering.
    """
    raw = AGENT_POST_TIMES.get(agent_id, "12:00")
    if raw != "random":
        return raw
    seed = f"{post_date.isoformat()}::{agent_id}"
    digest = int(hashlib.md5(seed.encode()).hexdigest()[:8], 16)
    start_h, end_h = 9, 22
    span_min = (end_h - start_h) * 60
    offset = digest % span_min
    h, m = divmod(offset, 60)
    return f"{start_h + h:02d}:{m:02d}"


@dataclass
class PostPayload:
    agent_id: str
    text: str
    mentions: list[str]
    kind: str
    parent_id: str | None
    refs: dict[str, Any]
    post_at: str

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "text": self.text,
            "mentions": self.mentions,
            "kind": self.kind,
            "parent_id": self.parent_id,
            "refs": self.refs,
            "post_at": self.post_at,
        }

    @classmethod
    def from_agent_output(cls, agent_id: str, raw: dict) -> "PostPayload":
        return cls(
            agent_id=agent_id,
            text=raw["text"],
            mentions=raw.get("mentions", []),
            kind=raw.get("kind", "trade"),
            parent_id=raw.get("parent_id"),
            refs=raw.get("refs", {}),
            post_at=raw.get("post_at", AGENT_POST_TIMES.get(agent_id, "12:00")),
        )


def build_post_prompt(
    agent_id: str,
    all_results: dict[str, dict],
    oracle_blog: str | None = None,
) -> str:
    """Build the post-generation prompt for a single trading agent.

    When `oracle_blog` (the Oracle's body_md for today) is provided, it is
    injected as a context block so the agent can react to the Oracle's
    framing of the day, not just to other agents' raw moves.
    """
    display = AGENT_DISPLAY_NAMES[agent_id]
    voice = AGENT_VOICE[agent_id]
    schedule = AGENT_POST_TIMES[agent_id]

    own = all_results.get(agent_id, {})
    own_section = f"YOUR COMMENTARY: {own.get('commentary', 'No commentary.')}\n"
    own_trades = own.get("trades", [])
    if own_trades:
        own_section += "YOUR TRADES:\n"
        for t in own_trades:
            own_section += f"  - {t['action']} {t.get('shares', '')} {t['ticker']}: {t.get('reasoning', '')}\n"
    else:
        own_section += "YOUR TRADES: None today.\n"

    others_section = "OTHER AGENTS TODAY:\n"
    for other_id, res in all_results.items():
        if other_id == agent_id:
            continue
        name = AGENT_DISPLAY_NAMES.get(other_id, other_id)
        commentary = res.get("commentary", "")
        trades = res.get("trades", [])
        others_section += f"\n  {name}:\n    Commentary: {commentary}\n"
        if trades:
            for t in trades:
                others_section += f"    - {t['action']} {t.get('shares', '')} {t['ticker']}: {t.get('reasoning', '')}\n"

    oracle_section = (
        f"\nORACLE'S NARRATIVE TODAY:\n{oracle_blog}\n" if oracle_blog else ""
    )

    return f"""You are {display} writing short posts for the Midas Feed.

VOICE: {voice}

{own_section}
{others_section}{oracle_section}

INSTRUCTIONS:
- Write 1-3 posts. Soft 280-char guideline per post (readability, not a hard limit).
- At least one post about your own moves today. Prefix every ticker with $ ($BTC-EUR, $MSFT, $GLD) — the feed linkifies them to /ticker/SLUG.
- If another agent did something worth reacting to, write a post about it. Mention them by display name.
- Stay in character: {voice}
- Be specific — real numbers, real tickers, real reasoning. No vague platitudes.
- Your posts appear in the feed around {schedule}.

OUTPUT — JSON array, no other text:
[{{"text": "...", "mentions": ["agent-id-if-mentioned"], "kind": "trade|roast|market-take"}}]
"""


def parse_post_response(agent_id: str, response_text: str) -> list[PostPayload]:
    text = response_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1
        end = len(lines) - 1 if lines[-1].strip().startswith("```") else len(lines)
        text = "\n".join(lines[start:end]).strip()
    raw = json.loads(text)
    return [PostPayload.from_agent_output(agent_id, r) for r in raw]


def save_daily_posts(post_date: date, all_posts: dict[str, list[PostPayload]]) -> Path:
    posts_dir = get_config().posts_dir
    posts_dir.mkdir(parents=True, exist_ok=True)
    path = posts_dir / f"{post_date.isoformat()}.json"
    out = {aid: [p.to_dict() for p in posts] for aid, posts in all_posts.items()}
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return path
