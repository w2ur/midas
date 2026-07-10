"""Self-describing catalog for the backtester UI.

Derives the signal-strategy presets and the offerable universes from the
committed engine config (data/strategies/*.json + engine.universes), so the
frontend never hardcodes strategy lists and cannot drift from the engine.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from engine.universes import resolve_universe

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_STRATEGIES_DIR = _PROJECT_ROOT / "data" / "strategies"

DATE_MIN = "2010-01-01"

# Allocation-shape baselines — belong to a future allocation UI, not the v1
# signal catalog. Excluded by id (their universes resolve, so a universe filter
# alone would not drop them).
_BASELINE_IDS = frozenset(
    {
        "baseline-60-40",
        "baseline-equal-weight",
        "baseline-voo-hold",
        "coin-flip-baseline",
    }
)

# Universes surfaced in the dropdown (index/alt only; crypto/forex/metals are
# out of the signal-strategy v1 scope). Filtered at request time to those that
# actually resolve, so a renamed/removed universe silently drops out.
_SIGNAL_UNIVERSE_IDS = [
    "sp500",
    "dow30",
    "nasdaq100",
    "cac40",
    "dax",
    "ftse100",
    "stoxx-600",
    "congress",
    "insiders",
    "high-short",
    "etf-broad",
    "etf-sectors",
]

_UNIVERSE_LABELS = {
    "sp500": "S&P 500",
    "dow30": "Dow 30",
    "nasdaq100": "Nasdaq 100",
    "cac40": "CAC 40",
    "dax": "DAX",
    "ftse100": "FTSE 100",
    "stoxx-600": "STOXX 600",
    "congress": "Congress (STOCK Act)",
    "insiders": "Insider buying",
    "high-short": "High short interest",
    "etf-broad": "Broad ETFs",
    "etf-sectors": "Sector ETFs",
}


def _load_specs() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(_STRATEGIES_DIR.glob("*.json"))]


def _build_presets() -> list[dict]:
    seen: set[tuple] = set()
    presets: list[dict] = []
    for spec in _load_specs():
        sid = spec["id"]
        if sid in _BASELINE_IDS:
            continue
        universe = spec["universe"]
        try:
            resolve_universe(universe)
        except KeyError:
            continue  # unimplemented/unknown universe (e.g. dividend-aristocrats)
        rules = spec["rules"]
        key = (
            universe,
            spec["selector"],
            spec["manager"],
            rules["max_positions"],
            rules["max_position_pct"],
            rules["min_hold_days"],
        )
        if key in seen:
            continue  # functional duplicate under the signal API
        seen.add(key)
        presets.append(
            {
                "id": sid,
                "label": spec["name"],
                "selector": spec["selector"],
                "manager": spec["manager"],
                "rules": {
                    "max_positions": rules["max_positions"],
                    "max_position_pct": rules["max_position_pct"],
                    "min_hold_days": rules["min_hold_days"],
                },
                "default_universe": universe,
            }
        )
    return presets


def _build_universes(presets: list[dict]) -> list[dict]:
    ids: list[str] = list(
        dict.fromkeys(_SIGNAL_UNIVERSE_IDS + [p["default_universe"] for p in presets])
    )
    out: list[dict] = []
    for uid in ids:
        try:
            resolve_universe(uid)
        except KeyError:
            continue
        out.append({"id": uid, "label": _UNIVERSE_LABELS.get(uid, uid)})
    return out


def build_catalog(today: date) -> dict:
    presets = _build_presets()
    return {
        "presets": presets,
        "universes": _build_universes(presets),
        "date_bounds": {"min": DATE_MIN, "max": today.isoformat()},
        "currencies": ["EUR", "USD"],
    }
