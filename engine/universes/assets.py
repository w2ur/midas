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


def get_bearish_etf_tickers() -> list[str]:
    """Return inverse ETFs that express bearish views without true shorting.

    These are regular long positions whose value rises when an index falls.
    Note: designed for daily returns — multi-day holds suffer volatility decay.
    """
    return [
        "SH",    # ProShares Short S&P 500 (-1x)
        "PSQ",   # ProShares Short QQQ (-1x)
        "DOG",   # ProShares Short Dow 30 (-1x)
        "RWM",   # ProShares Short Russell 2000 (-1x)
        "SDS",   # ProShares UltraShort S&P 500 (-2x)
        "SPXS",  # Direxion Daily S&P 500 Bear 3x
        "SPXU",  # ProShares UltraPro Short S&P 500 (-3x)
        "SQQQ",  # ProShares UltraPro Short QQQ (-3x)
    ]
