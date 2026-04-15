"""Asset class universe resolvers — crypto, forex, metals/commodities.

All lists are static (no API calls). Tickers use yfinance format.
"""

from __future__ import annotations


def get_crypto_tickers() -> list[str]:
    """Return the top 20 crypto tickers in yfinance format (XXX-USD)."""
    return [
        "BTC-USD",
        "ETH-USD",
        "BNB-USD",
        "SOL-USD",
        "XRP-USD",
        "DOGE-USD",
        "ADA-USD",
        "AVAX-USD",
        "SHIB-USD",
        "DOT-USD",
        "LINK-USD",
        "LTC-USD",
        "BCH-USD",
        "UNI-USD",
        "MATIC-USD",
        "XLM-USD",
        "ATOM-USD",
        "FIL-USD",
        "HBAR-USD",
        "ICP-USD",
    ]


def get_forex_tickers() -> list[str]:
    """Return major forex pairs in yfinance format (XXXYYY=X)."""
    return [
        "EURUSD=X",
        "GBPUSD=X",
        "USDJPY=X",
        "AUDUSD=X",
        "USDCAD=X",
        "USDCHF=X",
        "NZDUSD=X",
        "EURGBP=X",
        "EURJPY=X",
        "GBPJPY=X",
    ]


def get_metals_tickers() -> list[str]:
    """Return metals and commodities tickers (futures + ETFs) in yfinance format."""
    return [
        "GC=F",   # Gold futures
        "SI=F",   # Silver futures
        "PL=F",   # Platinum futures
        "CL=F",   # Crude Oil WTI futures
        "HG=F",   # Copper futures
        "GLD",    # SPDR Gold ETF
        "SLV",    # iShares Silver ETF
        "USO",    # United States Oil Fund ETF
    ]


def get_voo_only() -> list[str]:
    """Return VOO as a single-ticker universe for buy-and-hold baseline."""
    return ["VOO"]


def get_classic_60_40() -> list[str]:
    """Return VOO + BND for the classic 60/40 portfolio baseline."""
    return ["VOO", "BND"]
