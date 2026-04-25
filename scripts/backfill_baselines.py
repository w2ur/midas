"""Rebuild data/baselines/ for Day 1 → today.

Idempotent: always overwrites. Pulls each agent's universe and
max_positions from the constants below, which mirror the
.claude/agents/<id>.md persona rules at the time this script
was written. Update both when an agent's persona changes.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from engine.baselines import DAY_ONE, build_all_baselines
from engine.universes.index import (
    get_cac40_tickers,
    get_dax_tickers,
    get_ftse100_tickers,
    get_sp500_tickers,
    get_stoxx600_tickers,
)
from engine.universes.assets import (
    get_bearish_etf_ucits_tickers,
    get_commodities_eur_tickers,
    get_crypto_eur_tickers,
    get_crypto_tickers,
    get_forex_tickers,
    get_metals_tickers,
)


def _union(*lists: list[str]) -> list[str]:
    return sorted({t for lst in lists for t in lst})


# Mirrors the "Universe:" line in each .claude/agents/<id>.md persona.
# Keep in sync manually when personas change.
AGENT_UNIVERSES: dict[str, list[str]] = {
    # crypto-top20-eur (Kraken EUR pairs)
    "satoshi": get_crypto_eur_tickers(),
    # stoxx-600 + cac40 + dax + ftse100 + crypto-top20-eur + commodities-eur + bearish-etfs-ucits
    "yolo-sapiens-eur": _union(
        get_stoxx600_tickers(),
        get_cac40_tickers(),
        get_dax_tickers(),
        get_ftse100_tickers(),
        get_crypto_eur_tickers(),
        get_commodities_eur_tickers(),
        get_bearish_etf_ucits_tickers(),
    ),
    # ANYTHING: sp500 + crypto-usd + forex + metals + bearish-etfs-ucits
    "yolo-sapiens-usd": _union(
        get_sp500_tickers(),
        get_crypto_tickers(),
        get_forex_tickers(),
        get_metals_tickers(),
        get_bearish_etf_ucits_tickers(),
    ),
    # commodities-eur UCITS ETFs
    "goldfinger": get_commodities_eur_tickers(),
    # major + minor forex pairs
    "monsieur-forex": get_forex_tickers(),
    # stoxx-600 + bearish-etfs-ucits (up to 2x)
    "sharp-shooter-eur": _union(
        get_stoxx600_tickers(),
        get_cac40_tickers(),
        get_dax_tickers(),
        get_ftse100_tickers(),
        get_bearish_etf_ucits_tickers(),
    ),
    # S&P 500 + bearish-etfs-ucits (up to 2x)
    "sharp-shooter-usd": _union(
        get_sp500_tickers(),
        get_bearish_etf_ucits_tickers(),
    ),
    # stoxx-600 (focused: cac40, dax, ftse100)
    "steady-eddie-eur": _union(
        get_stoxx600_tickers(),
        get_cac40_tickers(),
        get_dax_tickers(),
        get_ftse100_tickers(),
    ),
    # S&P 500 constituents
    "steady-eddie-usd": get_sp500_tickers(),
    # ANY ticker in OHLCV store — use all major universes for broadest coverage
    "world": _union(
        get_sp500_tickers(),
        get_stoxx600_tickers(),
        get_cac40_tickers(),
        get_dax_tickers(),
        get_ftse100_tickers(),
        get_crypto_eur_tickers(),
        get_crypto_tickers(),
        get_forex_tickers(),
        get_commodities_eur_tickers(),
        get_bearish_etf_ucits_tickers(),
    ),
}

# Mirrors "Max positions:" from each persona.
AGENT_MAX_POSITIONS: dict[str, int] = {
    "satoshi": 8,
    "yolo-sapiens-eur": 5,
    "yolo-sapiens-usd": 5,
    "goldfinger": 6,
    "monsieur-forex": 6,
    "sharp-shooter-eur": 8,
    "sharp-shooter-usd": 8,
    "steady-eddie-eur": 10,
    "steady-eddie-usd": 10,
    "world": 12,
}


def main() -> None:
    today = date.today()
    build_all_baselines(
        universes_by_agent=AGENT_UNIVERSES,
        from_date=DAY_ONE,
        to_date=today,
        max_positions_by_agent=AGENT_MAX_POSITIONS,
    )
    print(f"Baselines written to data/baselines/ for {DAY_ONE} → {today}")


if __name__ == "__main__":
    main()
