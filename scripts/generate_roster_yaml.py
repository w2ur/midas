"""One-shot: emit roster.yaml from the current hardcoded cast structures.

Reads the legacy dicts + per-agent safety JSON and writes roster.yaml so the
midas-live cast is reproduced verbatim. Run once; the output is committed.

Universes are emitted as registry NAMES (not resolved ticker lists) — keeping
roster.yaml ~70 readable lines. engine.config.resolve_agent_universe(spec)
reconstructs the byte-identical resolved tickers from these names, so the
coin-flip baselines are unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from engine.posts import AGENT_DISPLAY_NAMES, AGENT_POST_TIMES, AGENT_VOICE
from engine.baselines import AGENT_BENCHMARKS, DAY_ONE, GLOBAL_REFERENCE, INITIAL
from scripts.backfill_baselines import AGENT_MAX_POSITIONS

ROOT = Path(__file__).resolve().parents[1]
AGENT_CONFIG_DIR = ROOT / "data" / "agent_config"

# Named universes per agent (registry names from engine/universes/__init__.py
# _RESOLVERS). Mirrors AGENT_UNIVERSES in scripts.backfill_baselines: the
# parity test guards that resolve_agent_universe reproduces the legacy resolved
# tickers byte-for-byte.
AGENT_UNIVERSE_NAMES = {
    "satoshi": ["crypto-top20-eur"],
    "yolo-sapiens-eur": [
        "stoxx-600",
        "cac40",
        "dax",
        "ftse100",
        "crypto-top20-eur",
        "commodities-eur",
        "bearish-etfs-ucits",
    ],
    "yolo-sapiens-usd": [
        "sp500",
        "crypto-top20",
        "forex-majors",
        "metals-commodities",
        "bearish-etfs-ucits",
    ],
    "goldfinger": ["commodities-eur"],
    "monsieur-forex": ["forex-majors"],
    "sharp-shooter-eur": ["stoxx-600", "cac40", "dax", "ftse100", "bearish-etfs-ucits"],
    "sharp-shooter-usd": ["sp500", "bearish-etfs-ucits"],
    "steady-eddie-eur": ["stoxx-600", "cac40", "dax", "ftse100"],
    "steady-eddie-usd": ["sp500"],
    "world": [
        "sp500",
        "stoxx-600",
        "cac40",
        "dax",
        "ftse100",
        "crypto-top20-eur",
        "crypto-top20",
        "forex-majors",
        "commodities-eur",
        "bearish-etfs-ucits",
    ],
}


def _safety(agent_id: str) -> dict:
    path = AGENT_CONFIG_DIR / f"{agent_id}.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {
        "max_order_notional": raw["max_order_notional"],
        "max_orders_per_day": raw["max_orders_per_day"],
        "daily_drawdown_halt_pct": raw["daily_drawdown_halt_pct"],
        "allowed_universe": raw.get("allowed_universe", []),
        "dry_run": raw.get("dry_run", False),
    }


def build() -> dict:
    agents: dict = {}
    # Trading roster — order follows AGENT_POST_TIMES (load-bearing).
    for agent_id in AGENT_POST_TIMES:
        bench = AGENT_BENCHMARKS.get(agent_id)
        agents[agent_id] = {
            "display_name": AGENT_DISPLAY_NAMES[agent_id],
            "voice": AGENT_VOICE.get(agent_id, ""),
            "post_time": AGENT_POST_TIMES[agent_id],
            "home_currency": bench.currency if bench else "EUR",
            "initial_capital": INITIAL,
            "max_positions": AGENT_MAX_POSITIONS.get(agent_id, 5),
            "universe": AGENT_UNIVERSE_NAMES.get(agent_id, []),
            "benchmark": (
                {
                    "label": bench.label,
                    "ticker": bench.ticker,
                    "currency": bench.currency,
                }
                if bench
                else None
            ),
            "persona": f"{agent_id}.md",
            "role": "trader",
            "safety": _safety(agent_id),
        }
    # Oracle (narrator) — not in AGENT_POST_TIMES.
    agents["the-oracle"] = {
        "display_name": AGENT_DISPLAY_NAMES["the-oracle"],
        "voice": AGENT_VOICE.get("the-oracle", ""),
        "post_time": "",
        "role": "narrator",
        "persona": "the-oracle.md",
    }
    return {
        "globals": {
            "day_one": DAY_ONE.isoformat(),
            "currencies": ["EUR", "USD"],
            "initial_capital": INITIAL,
            "global_reference": {
                "label": GLOBAL_REFERENCE.label,
                "ticker": GLOBAL_REFERENCE.ticker,
                "currency": GLOBAL_REFERENCE.currency,
            },
            "agents_dir": ".claude/agents",
        },
        "agents": agents,
    }


if __name__ == "__main__":
    out = ROOT / "roster.yaml"
    out.write_text(
        yaml.safe_dump(
            build(), sort_keys=False, allow_unicode=True, default_flow_style=False
        ),
        encoding="utf-8",
    )
    print(f"Wrote {out}")
