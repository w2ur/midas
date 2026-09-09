"""Index universe resolvers.

US: S&P 500, Dow 30, Nasdaq 100.
EU: CAC 40, DAX, FTSE 100, STOXX Europe 600.

Universe lists live in `data/universes/{name}.json`, **committed** to the
repo. The cloud sandbox has no outbound HTTP, so resolvers must NEVER hit
Wikipedia, Slickcharts, DWS or Yahoo at runtime. File presence is
authoritative. Periodic refresh runs out-of-band (manual
`scripts/refresh_universes.py` or the GitHub Actions weekly cron
`refresh-universes.yml`) and commits the diff.

This module previously kept these files under `data/cache/universes/`
(gitignored) with a 24-hour TTL — that combination crashed every cloud
session whose cache was older than a day. Apr 29 incident.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
import urllib.request
from collections.abc import Callable

import pandas as pd

from engine.config import get_config

logger = logging.getLogger(__name__)

_WIKI_USER_AGENT = "midas-fund/0.1 (https://github.com/w2ur/midas; research)"


def _fetch_html_tables(url: str) -> list[pd.DataFrame]:
    """Fetch an HTML page with a descriptive User-Agent and parse its tables.

    Wikipedia (and Slickcharts) reject pandas' default Python-urllib UA, so we
    fetch the HTML ourselves before handing it to pd.read_html. Used for both
    the Wikipedia index pages and the Slickcharts Nasdaq-100 source.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _WIKI_USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8")
    return pd.read_html(io.StringIO(html))


def _largest_table_with_column(
    tables: list[pd.DataFrame], column: str
) -> pd.DataFrame | None:
    """Return the largest table containing `column` in its columns.

    Robust against Wikipedia page layout changes: avoids picking small
    "examples" or "recent changes" tables that happen to share a column name.
    """
    candidates = [t for t in tables if column in [str(c) for c in t.columns]]
    if not candidates:
        return None
    return max(candidates, key=len)


def _read_data(name: str) -> list[str] | None:
    """Return committed tickers for `name`, or None if the file is missing.

    No TTL: the file is the source of truth. If you want to refresh from
    Wikipedia, call `refresh_<name>()` explicitly.
    """
    path = get_config().universes_dir / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_data(name: str, tickers: list[str]) -> None:
    """Persist tickers to `data/universes/{name}.json`."""
    data_dir = get_config().universes_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / f"{name}.json").write_text(json.dumps(tickers), encoding="utf-8")


def _normalise(ticker: str) -> str:
    """Replace dots with hyphens for yfinance compatibility (BRK.B → BRK-B)."""
    return ticker.replace(".", "-").strip()


# ---------------------------------------------------------------------------
# S&P 500
# ---------------------------------------------------------------------------


def get_sp500_tickers() -> list[str]:
    """Return committed S&P 500 constituents.

    Reads `data/universes/sp500.json`. Falls back to a Wikipedia refresh ONLY
    when the file is missing — which should never happen in production since
    the file is committed.
    """
    cached = _read_data("sp500")
    if cached is not None:
        return cached
    return refresh_sp500()


def refresh_sp500() -> list[str]:
    """Re-fetch S&P 500 from Wikipedia and overwrite the committed file."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    tables = _fetch_html_tables(url)
    table = _largest_table_with_column(tables, "Symbol")
    if table is None:
        raise RuntimeError("S&P 500: no 'Symbol' column on Wikipedia page")
    tickers = sorted({_normalise(str(t)) for t in table["Symbol"].tolist()})
    if len(tickers) < 100:
        raise RuntimeError(
            f"S&P 500: {len(tickers)} tickers — Wikipedia layout may have changed"
        )
    _write_data("sp500", tickers)
    return tickers


# ---------------------------------------------------------------------------
# Dow 30
# ---------------------------------------------------------------------------


def get_dow30_tickers() -> list[str]:
    """Return committed Dow Jones Industrial Average constituents."""
    cached = _read_data("dow30")
    if cached is not None:
        return cached
    return refresh_dow30()


def refresh_dow30() -> list[str]:
    """Re-fetch Dow 30 from Wikipedia and overwrite the committed file."""
    url = "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average"
    tables = _fetch_html_tables(url)
    table = _largest_table_with_column(tables, "Symbol")
    if table is None:
        raise RuntimeError("Dow 30: no 'Symbol' column on Wikipedia page")
    raw = [str(t) for t in table["Symbol"].dropna().tolist() if str(t) != "Symbol"]
    tickers = sorted({_normalise(t) for t in raw if t})
    if len(tickers) < 20:
        raise RuntimeError(
            f"Dow 30: {len(tickers)} tickers — Wikipedia layout may have changed"
        )
    _write_data("dow30", tickers)
    return tickers


# ---------------------------------------------------------------------------
# Nasdaq 100
# ---------------------------------------------------------------------------


def get_nasdaq100_tickers() -> list[str]:
    """Return committed Nasdaq-100 constituents."""
    cached = _read_data("nasdaq100")
    if cached is not None:
        return cached
    return refresh_nasdaq100()


# Candidate ticker-column header names on the Slickcharts Nasdaq-100 page,
# tried in order. Tolerant to a future header rename (Slickcharts has already
# forced one source change — see docstring below) without another crash.
_NASDAQ100_SYMBOL_COLUMNS: tuple[str, ...] = ("Symbol", "Ticker")


def refresh_nasdaq100() -> list[str]:
    """Re-fetch Nasdaq-100 from Slickcharts and overwrite the committed file.

    Source moved off Wikipedia on 2026-07-13: the en.wikipedia.org/wiki/Nasdaq-100
    article dropped its constituents table entirely (the "Components" section is
    now just an external link to nasdaq.com), so no column-name variant could
    recover it. Slickcharts publishes a clean weighted table with a "Symbol"
    column (~100 rows, dual-class shares like GOOGL/GOOG included). Column
    detection tries each of `_NASDAQ100_SYMBOL_COLUMNS` in turn so a future
    Slickcharts header rename (e.g. back to "Ticker") degrades gracefully
    instead of raising immediately.
    """
    url = "https://www.slickcharts.com/nasdaq100"
    tables = _fetch_html_tables(url)
    table = None
    symbol_col = None
    for candidate in _NASDAQ100_SYMBOL_COLUMNS:
        table = _largest_table_with_column(tables, candidate)
        if table is not None:
            symbol_col = candidate
            break
    if table is None:
        raise RuntimeError(
            "Nasdaq-100: no "
            f"{' or '.join(repr(c) for c in _NASDAQ100_SYMBOL_COLUMNS)} "
            "column on Slickcharts page"
        )
    raw = [str(t) for t in table[symbol_col].dropna().tolist() if str(t) != symbol_col]
    tickers = sorted({_normalise(t) for t in raw if t})
    if len(tickers) < 90:
        raise RuntimeError(
            f"Nasdaq-100: {len(tickers)} tickers — Slickcharts layout may have changed"
        )
    _write_data("nasdaq100", tickers)
    return tickers


# ---------------------------------------------------------------------------
# EU indices — CAC 40, DAX, FTSE 100, STOXX Europe 600
# ---------------------------------------------------------------------------

# Country → yfinance exchange suffix. For STOXX 600 this is a PREFERENCE among
# the listings Yahoo returns for an ISIN (see `_pick_symbol`), never something
# appended to a code: the export's country is a domicile (Prosus is "China",
# Airbus "Netherlands") and the home market can differ from it.
_STOXX_COUNTRY_SUFFIX: dict[str, str] = {
    "Austria": ".VI",
    "Belgium": ".BR",
    "Denmark": ".CO",
    "Finland": ".HE",
    "France": ".PA",
    "Germany": ".DE",
    "Greece": ".AT",
    "Ireland": ".IR",
    "Italy": ".MI",
    "Luxembourg": ".LU",
    "Netherlands": ".AS",
    "Norway": ".OL",
    "Poland": ".WA",
    "Portugal": ".LS",
    "Spain": ".MC",
    "Sweden": ".ST",
    "Switzerland": ".SW",
    "United Kingdom": ".L",
    # Jersey / Bermuda / Israel companies often list on LSE
    "Jersey": ".L",
    "Bermuda": ".L",
    "Israel": ".L",
}


def _clean_ticker(raw: object) -> str | None:
    s = str(raw).strip()
    if not s or s.lower() in ("ticker", "nan", "none", "—"):
        return None
    if "[" in s:
        s = s.split("[", 1)[0].strip()
    return s or None


def get_cac40_tickers() -> list[str]:
    """Return committed CAC 40 constituents."""
    cached = _read_data("cac40")
    if cached is not None:
        return cached
    return refresh_cac40()


def refresh_cac40() -> list[str]:
    url = "https://en.wikipedia.org/wiki/CAC_40"
    tables = _fetch_html_tables(url)
    table = _largest_table_with_column(tables, "Ticker")
    if table is None:
        raise RuntimeError("CAC 40: no 'Ticker' column on Wikipedia page")
    tickers = sorted({t for t in (_clean_ticker(v) for v in table["Ticker"]) if t})
    if len(tickers) < 30:
        raise RuntimeError(f"CAC 40: {len(tickers)} tickers — layout changed")
    _write_data("cac40", tickers)
    return tickers


def get_dax_tickers() -> list[str]:
    """Return committed DAX constituents."""
    cached = _read_data("dax")
    if cached is not None:
        return cached
    return refresh_dax()


def refresh_dax() -> list[str]:
    url = "https://en.wikipedia.org/wiki/DAX"
    tables = _fetch_html_tables(url)
    table = _largest_table_with_column(tables, "Ticker")
    if table is None:
        raise RuntimeError("DAX: no 'Ticker' column on Wikipedia page")
    tickers = sorted({t for t in (_clean_ticker(v) for v in table["Ticker"]) if t})
    if len(tickers) < 30:
        raise RuntimeError(f"DAX: {len(tickers)} tickers — layout changed")
    _write_data("dax", tickers)
    return tickers


def get_ftse100_tickers() -> list[str]:
    """Return committed FTSE 100 constituents (.L suffix appended for yfinance)."""
    cached = _read_data("ftse100")
    if cached is not None:
        return cached
    return refresh_ftse100()


def refresh_ftse100() -> list[str]:
    url = "https://en.wikipedia.org/wiki/FTSE_100_Index"
    tables = _fetch_html_tables(url)
    table = _largest_table_with_column(tables, "Ticker")
    if table is None:
        raise RuntimeError("FTSE 100: no 'Ticker' column on Wikipedia page")
    tickers: set[str] = set()
    for raw in table["Ticker"]:
        t = _clean_ticker(raw)
        if t is None:
            continue
        if not t.endswith(".L"):
            t = f"{t}.L"
        tickers.add(t)
    result = sorted(tickers)
    if len(result) < 80:
        raise RuntimeError(f"FTSE 100: {len(result)} tickers — layout changed")
    _write_data("ftse100", result)
    return result


def get_stoxx600_tickers() -> list[str]:
    """Return committed STOXX Europe 600 constituents (Yahoo symbols)."""
    cached = _read_data("stoxx600")
    if cached is not None:
        return cached
    return refresh_stoxx600()


# ---------------------------------------------------------------------------
# STOXX Europe 600 — keyed on ISIN, resolved through the vendor's own lookup
# ---------------------------------------------------------------------------
#
# Until 2026-09-09 this index was scraped from Wikipedia's "Ticker" column with
# a country suffix appended. That column holds Reuters-style codes (AIRP, BNPP,
# CAGR, "AMBU B", "ATCOa"), not Yahoo symbols, so 120 of the 463 entries it
# produced had never served a single row (issue #36): four agents were handed
# them as tradable, every order on one died at the broker with NO_PRICE_DATA,
# and every nightly fetch printed ~120 "Quote not found" lines. The list was
# also stale — it lacked 270 current constituents and carried names that had
# left the index. No per-exchange rewrite rule fixes a code the vendor does not
# route, and a hand-typed override map of 120 entries goes stale the same way.
#
# An ISIN is the one identifier both sides agree on. The constituent list comes
# from DWS's export for the Xtrackers STOXX Europe 600 UCITS ETF 1C
# (LU0328475792), the only free source found that carries an ISIN per line:
# STOXX's own components CSV answers 404, Wikipedia has no ISIN column, and the
# iShares EXSA holdings file omits ISIN in every locale. Each ISIN is then put
# to Yahoo's search endpoint, which answers with the listings it actually
# serves — so a symbol in the committed file is one the nightly fetch can
# fetch, by construction. The tradable universe is defined by what the vendor
# can price, which is the property the old list lacked.
_STOXX600_CONSTITUENTS_URL = (
    "https://etf.dws.com/etfdata/export/LUX/ENG/csv/product/constituent/LU0328475792/"
)
_STOXX600_REQUIRED_COLUMNS = (
    "Constituent ISIN",
    "Constituent Name",
    "Constituent Country",
)
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

#: Seconds between two vendor lookups, and the back-off schedule after a rate
#: limit. Measured 2026-09-09 over 605 ISINs: unthrottled (~4/s) the endpoint
#: rate-limited 3 times; at 0.4 s spacing once, recovered by the first 10 s
#: sleep. 0.5 s puts a full pass at ~5 minutes, inside the workflow's timeout.
ISIN_LOOKUP_SPACING_S = 0.5
ISIN_LOOKUP_RETRY_SLEEPS_S = (10.0, 30.0, 60.0)

#: Refuse to overwrite the committed file when more than this share of the
#: export's ISINs did not resolve. Measured baseline 2026-09-09: 8 of 605
#: (1.3%) — loyalty/bonus-share ISINs (L'Oréal, Air Liquide), an Engie
#: preference line, a cash line with no name, two names Yahoo does not index by
#: ISIN (Kesko B, Vår Energi) and one only quoted on a German regional floor.
#: A vendor outage or a rate limit that outlasts the retry schedule reads
#: ~100%, and the last known-good file stays in place.
MAX_UNRESOLVED_ISIN_RATE = 0.05

#: Yahoo exchange codes a symbol must not come from. PNK is the OTC pink sheet:
#: an ADR or grey-market print, never the listing an EU desk trades.
_OTC_EXCHANGES = frozenset({"PNK"})
#: German regional floors. Yahoo lists many foreign names there (SAGAX B of
#: Stockholm answered only as EFE.F) with thin, often stale daily bars. Accepted
#: only for a German constituent, where Frankfurt is the home market.
_GERMAN_REGIONAL_EXCHANGES = frozenset(
    {"FRA", "STU", "MUN", "DUS", "BER", "HAM", "HAN"}
)


def _fetch_stoxx600_constituents(
    url: str = _STOXX600_CONSTITUENTS_URL,
) -> list[dict[str, str]]:
    """Return the export's ISIN-bearing rows as dicts keyed by column name.

    The export also lists cash (`_CURRENCYEUR`) and index-future lines whose
    "ISIN" is not ISIN-shaped; those are dropped here. A missing column is a
    layout change and raises, like the Wikipedia scrapers do.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _WIKI_USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    columns = [str(c).strip() for c in (reader.fieldnames or [])]
    missing = [c for c in _STOXX600_REQUIRED_COLUMNS if c not in columns]
    if missing:
        raise RuntimeError(
            f"STOXX 600: export lacks column(s) {missing} — layout changed"
        )
    rows: list[dict[str, str]] = []
    for row in reader:
        clean = {
            str(k).strip(): (v or "").strip() for k, v in row.items() if k is not None
        }
        if _ISIN_RE.match(clean.get("Constituent ISIN", "")):
            rows.append(clean)
    return rows


def _search_isin_quotes(
    isin: str, sleep: Callable[[float], None] | None = None
) -> list[dict]:
    """Ask Yahoo which listings it serves for `isin`, retrying a rate limit.

    Returns an empty list when the vendor answers with nothing or the retry
    schedule is exhausted — both leave the ISIN unresolved, and the caller's
    rate gate decides whether that is a bad line or a bad night.
    """
    import yfinance as yf  # network-only; keep import cost off the session path

    sleep = sleep or time.sleep  # bound at call time so tests can patch it
    for backoff in (*ISIN_LOOKUP_RETRY_SLEEPS_S, None):
        try:
            return list(
                yf.Search(isin, max_results=10, news_count=0, lists_count=0).quotes
            )
        except Exception as exc:  # YFRateLimitError, transport errors
            if backoff is None:
                logger.warning(
                    "STOXX 600: lookup for %s failed after retries — %s", isin, exc
                )
                return []
            sleep(backoff)
    return []  # unreachable; keeps the type checker honest


def _pick_symbol(isin: str, country: str, quotes: list[dict]) -> str | None:
    """Choose the listing to trade among what the vendor returned for `isin`.

    Preference order: the constituent's home-market suffix (a German name on
    Xetra beats its Frankfurt floor quote), then any real symbol over
    Stuttgart's `<ISIN>.SG` placeholders, then anything not on a German
    regional floor. A quote that survives only as a placeholder, or only on a
    regional floor for a non-German name, is refused rather than traded.
    """
    candidates = [
        q
        for q in quotes
        if q.get("quoteType") == "EQUITY"
        and q.get("symbol")
        and q.get("exchange") not in _OTC_EXCHANGES
    ]
    if not candidates:
        return None
    suffix = _STOXX_COUNTRY_SUFFIX.get(country)

    def rank(q: dict) -> tuple[int, int, int]:
        symbol, exchange = q["symbol"], q.get("exchange")
        return (
            0 if suffix and symbol.endswith(suffix) else 1,
            0 if isin not in symbol else 1,
            0 if exchange not in _GERMAN_REGIONAL_EXCHANGES else 1,
        )

    best = min(candidates, key=rank)
    if isin in best["symbol"]:
        return None
    if best.get("exchange") in _GERMAN_REGIONAL_EXCHANGES and suffix != ".DE":
        return None
    return best["symbol"]


def resolve_isins(
    constituents: list[dict[str, str]],
    *,
    lookup: Callable[[str], list[dict]] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Map each constituent's ISIN to a Yahoo symbol.

    Returns `(resolved, unresolved)`: `resolved` is `{isin: symbol}`,
    `unresolved` lists `(isin, name)` for every line the vendor could not
    place. Lookups are spaced `ISIN_LOOKUP_SPACING_S` apart.
    """
    # Resolved at call time, not bound as defaults: a default captures the
    # original `time.sleep`, which is how the first version of the test suite
    # sat through 250 s of real throttling.
    lookup = lookup or _search_isin_quotes
    sleep = sleep or time.sleep
    resolved: dict[str, str] = {}
    unresolved: list[tuple[str, str]] = []
    for i, row in enumerate(constituents):
        if i:
            sleep(ISIN_LOOKUP_SPACING_S)
        isin = row["Constituent ISIN"]
        symbol = _pick_symbol(isin, row.get("Constituent Country", ""), lookup(isin))
        if symbol is None:
            unresolved.append((isin, row.get("Constituent Name", "")))
        else:
            resolved[isin] = symbol
    return resolved, unresolved


def refresh_stoxx600() -> list[str]:
    constituents = _fetch_stoxx600_constituents()
    if len(constituents) < 400:
        raise RuntimeError(
            f"STOXX 600: export lists {len(constituents)} ISINs — layout changed"
        )
    resolved, unresolved = resolve_isins(constituents)
    rate = len(unresolved) / len(constituents)
    if rate > MAX_UNRESOLVED_ISIN_RATE:
        raise RuntimeError(
            f"STOXX 600: {len(unresolved)} of {len(constituents)} ISINs unresolved "
            f"({rate:.0%}, limit {MAX_UNRESOLVED_ISIN_RATE:.0%}) — vendor lookup "
            "unavailable; committed file left at its last known-good value"
        )
    if unresolved:
        logger.warning(
            "STOXX 600: %d of %d ISINs did not resolve to a tradable listing and "
            "are left out: %s",
            len(unresolved),
            len(constituents),
            ", ".join(f"{isin} ({name or 'unnamed'})" for isin, name in unresolved),
        )
    result = sorted(set(resolved.values()))
    if len(result) < 400:
        raise RuntimeError(f"STOXX 600: {len(result)} symbols — layout changed")
    _write_data("stoxx600", result)
    return result


# ---------------------------------------------------------------------------
# Bulk refresh
# ---------------------------------------------------------------------------


# Canonical {name: refresher-function-name} mapping — the single source of
# truth for which indexes exist. `scripts/refresh_universes.py` derives its
# skip report from these keys, so adding an index here is the only change
# needed. Values are attribute names resolved at call time (late-bound) so
# tests can monkeypatch the individual refresh_* functions.
INDEX_REFRESHERS = {
    "sp500": "refresh_sp500",
    "dow30": "refresh_dow30",
    "nasdaq100": "refresh_nasdaq100",
    "cac40": "refresh_cac40",
    "dax": "refresh_dax",
    "ftse100": "refresh_ftse100",
    "stoxx600": "refresh_stoxx600",
}


def refresh_all_indexes() -> dict[str, int]:
    """Re-fetch every index universe from its upstream source and overwrite
    the committed files.

    Sources: Wikipedia for the S&P 500, Dow 30, CAC 40, DAX and FTSE 100;
    Slickcharts for the Nasdaq-100; for the STOXX 600, DWS's constituent
    export resolved ISIN by ISIN through Yahoo's lookup (see
    `refresh_stoxx600`), which is the slow one — a few minutes, throttled.

    Used by `scripts/refresh_universes.py` and the weekly GitHub Actions cron.
    Each index refreshes independently: a scraper that raises (e.g. an
    upstream layout change like the 2026-07-13 Nasdaq-100/Wikipedia break)
    logs a warning and is skipped rather than aborting the whole run — one
    broken source must never take the other six indexes down with it. The
    committed file for a skipped index is left untouched at its last known
    -good value. Returns {name: ticker_count} for indexes that succeeded
    only; a failed index is simply absent from the result (callers alert on
    the gap — the weekly workflow exits non-zero so the failure still emails).
    """
    results: dict[str, int] = {}
    for name, fn_name in INDEX_REFRESHERS.items():
        refresher = globals()[fn_name]
        try:
            results[name] = len(refresher())
        except Exception as exc:
            logger.warning("refresh_all_indexes: skipping %s — %s", name, exc)
    return results
