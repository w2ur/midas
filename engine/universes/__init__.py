"""engine.universes — aggregate re-exports + universe-name dispatch."""

from __future__ import annotations

from engine.universes.index import (
    get_sp500_tickers, get_dow30_tickers, get_nasdaq100_tickers,
    get_cac40_tickers, get_dax_tickers, get_ftse100_tickers, get_stoxx600_tickers,
)
from engine.universes.assets import (
    get_crypto_tickers, get_forex_tickers, get_metals_tickers,
    get_voo_only, get_classic_60_40,
    get_bearish_etf_tickers, get_crypto_eur_tickers,
    get_commodities_eur_tickers, get_bearish_etf_ucits_tickers,
)
from engine.universes.alternative import (
    get_congressional_tickers, get_insider_tickers, get_high_short_tickers,
)


_RESOLVERS = {
    "sp500": get_sp500_tickers, "dow30": get_dow30_tickers, "nasdaq100": get_nasdaq100_tickers,
    "cac40": get_cac40_tickers, "dax": get_dax_tickers, "ftse100": get_ftse100_tickers,
    "stoxx-600": get_stoxx600_tickers,
    "crypto-top20": get_crypto_tickers, "crypto-top20-eur": get_crypto_eur_tickers,
    "forex-majors": get_forex_tickers,
    "metals-commodities": get_metals_tickers, "commodities-eur": get_commodities_eur_tickers,
    "single-voo": get_voo_only, "classic-60-40": get_classic_60_40,
    "bearish-etfs": get_bearish_etf_tickers, "bearish-etfs-ucits": get_bearish_etf_ucits_tickers,
    "congress": get_congressional_tickers, "insiders": get_insider_tickers,
    "high-short": get_high_short_tickers,
    "dividend-aristocrats": lambda: [],  # placeholder
    "etf-sectors": lambda: [],
    "etf-broad": lambda: [],
    "13f-whales": lambda: [],
}


def resolve_universe(name: str) -> list[str]:
    """Return the tickers for a named universe.

    Raises KeyError for unknown names. Callers should check membership against
    VALID_UNIVERSES (engine.types) before calling if they want a cleaner error.
    """
    if name not in _RESOLVERS:
        raise KeyError(f"Unknown universe: {name!r}")
    return list(_RESOLVERS[name]())


__all__ = ["resolve_universe"]
