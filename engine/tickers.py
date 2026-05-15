"""Ticker name registry — maps symbol → human-readable name + asset type.

The registry is committed to git at data/tickers.json so the site can read it
at build time and so the sandboxed daily-session agent can see it. It is
populated and refreshed by scripts/fetch_ohlcv.py, which already calls
yfinance for every symbol once a week.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = _PROJECT_ROOT / "data" / "tickers.json"


class TickerInfo(TypedDict):
    name: str | None
    type: str  # "equity" | "etf" | "crypto" | "forex" | "unknown"


Registry = dict[str, TickerInfo]


def load_registry(path: Path = DEFAULT_PATH) -> Registry:
    """Load the registry from disk. Returns {} when the file is missing."""
    if not path.exists():
        return {}
    with path.open() as f:
        return json.load(f)


def save_registry(reg: Registry, path: Path = DEFAULT_PATH) -> None:
    """Write the registry to disk, sorted by symbol for diff stability."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {k: reg[k] for k in sorted(reg)}
    with path.open("w") as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)
        f.write("\n")


def merge(existing: Registry, fresh: Registry) -> Registry:
    """Merge a freshly-fetched registry into the existing one.

    Rule: when ``fresh[key].name`` is ``None``, keep the existing entry
    intact (a transient yfinance failure must not blank out a known name).
    Otherwise replace the existing entry wholesale.
    """
    out: Registry = dict(existing)
    for key, info in fresh.items():
        if info.get("name") is None and key in out and out[key].get("name") is not None:
            continue
        out[key] = info
    return out


def resolve_name(symbol: str, info: dict | None) -> TickerInfo:
    raise NotImplementedError("resolve_name is implemented in Task 2")
