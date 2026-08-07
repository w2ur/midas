"""The W1 rails, checked against the live desk's actual committed state.

Live-only (see LIVE_ONLY_TESTS in scripts/sync_core.py): reads this repo's
committed portfolios, pending orders, inbox ledger, universes and OHLCV store,
none of which exist in midas-core.

Two jobs, and they pull in opposite directions on purpose:

* **Coverage** — nothing tradable may be undenominable. `ticker_currency`
  returns `None` now instead of guessing USD, which converts a silent
  mispricing into a `CURRENCY_UNRESOLVED` rejection. That is only an
  improvement if the maps actually cover what the desk trades.
* **No false positives** — the new price and trigger bands must not refuse
  anything the desk has legitimately done. A rail that would have rejected
  real history is a rail that will reject real trades on Monday.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from engine.config import get_config
from engine.paper_broker import (
    TRIGGER_LEVEL_MAX,
    TRIGGER_LEVEL_MIN,
    _price_out_of_band,
)
from engine.quotes import (
    _load_registry_currencies,
    _load_ticker_currency_overrides,
    latest_price,
    ticker_currency,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _held_tickers() -> set[str]:
    out: set[str] = set()
    for path in (REPO_ROOT / "data" / "portfolios").glob("*/portfolio.json"):
        book = json.loads(path.read_text(encoding="utf-8"))
        for position in book.get("positions", []):
            out.add(position["ticker"])
    return out


def _pending_orders() -> list[dict]:
    out: list[dict] = []
    for subdir in ("pending", "manager-pending"):
        for path in (REPO_ROOT / "data" / "orders" / subdir).glob("*.json"):
            out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def _universe_tickers() -> set[str]:
    out: set[str] = set()
    for path in (REPO_ROOT / "data" / "universes").glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = (
            raw if isinstance(raw, list) else raw.get("tickers", raw.get("symbols", []))
        )
        for item in items:
            ticker = item if isinstance(item, str) else item.get("ticker")
            if ticker:
                out.add(ticker)
    return out


# ---------------------------------------------------------------------------
# W1.4 — currency coverage
# ---------------------------------------------------------------------------


def test_every_held_and_pending_ticker_is_in_the_maps():
    """Held and pending tickers must resolve from layers 1-2, not the suffix.

    The strict bar applies here and not to the whole universe because these
    are the tickers with real state behind them: a position being valued
    every evening, or an armed order that will fill. For those, "the suffix
    says EUR" is not good enough — the vendor's own answer is available and
    is what the override map and registry exist to carry.

    Measured 2026-08-07: 40 held, 40 pending, 0 of either on the heuristic.
    """
    maps = set(_load_ticker_currency_overrides()) | set(_load_registry_currencies())
    tickers = _held_tickers() | {o["ticker"] for o in _pending_orders()}
    assert tickers, (
        "no held or pending tickers found — fixture is not exercising anything"
    )

    missing = sorted(t for t in tickers if t not in maps)
    assert missing == [], (
        "these tickers have real state but no vendor-sourced currency; add them to "
        f"data/ticker_currencies.json or re-run scripts/fetch_ohlcv.py: {missing}"
    )


def test_no_universe_ticker_is_undenominable():
    """Every tradable ticker must resolve to *something*.

    Deliberately weaker than the test above. 130 of the 1,046 universe
    tickers resolve through the suffix table rather than the maps (`.OL`,
    `.PA`, `.MC`, `.CO`, `.VI`, `.AS`, `.DE`), and that is acceptable for
    names nobody holds: a documented exchange→currency mapping is a
    different thing from the blind USD default this replaced. What is not
    acceptable is a ticker an agent could name in an order and that the
    broker would then have to refuse.
    """
    unresolved = sorted(t for t in _universe_tickers() if ticker_currency(t) is None)
    assert unresolved == [], (
        f"universe tickers with no resolvable currency: {unresolved}"
    )


# ---------------------------------------------------------------------------
# W1.2 / W1.3 — the bands must not refuse real history
# ---------------------------------------------------------------------------


def _filled_fills() -> list[tuple[str, date, str, float]]:
    """(order_id, trade_date, ticker, fill_price) for every committed fill.

    The ticker is not in the inbox line, so it is joined back from the
    outbox by order_id — the same join the site's trade cards make.
    """
    outbox_tickers: dict[str, str] = {}
    for path in (REPO_ROOT / "data" / "orders" / "outbox").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                order = json.loads(line)
            except json.JSONDecodeError:
                continue
            if order.get("order_id") and order.get("ticker"):
                outbox_tickers[order["order_id"]] = order["ticker"]

    out: list[tuple[str, date, str, float]] = []
    for path in sorted((REPO_ROOT / "data" / "orders" / "inbox").glob("*.jsonl")):
        trade_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                fill = json.loads(line)
            except json.JSONDecodeError:
                continue
            if fill.get("status") != "filled":
                continue
            ticker = outbox_tickers.get(fill.get("order_id", ""))
            price = fill.get("fill_price")
            if ticker and isinstance(price, (int, float)):
                out.append((fill["order_id"], trade_date, ticker, float(price)))
    return out


def test_the_price_band_would_not_have_refused_any_committed_fill():
    """Replay: no fill the desk has ever booked sits outside the band.

    This is the false-positive control for PRICE_IMPLAUSIBLE, and it is the
    half that matters — catching a 100x error is easy, doing it without
    refusing ordinary trades is the design constraint. Note the ledger has
    been reconciled onto ISO units (2026-08-07), so this replays the
    corrected basis, which is the basis Monday's fills will use.
    """
    fills = _filled_fills()
    assert len(fills) > 100, f"expected the full committed ledger, joined {len(fills)}"

    refused = []
    for order_id, trade_date, ticker, price in fills:
        previous = latest_price(ticker, trade_date - timedelta(days=1))
        if previous is None:
            continue
        if _price_out_of_band(price, previous.price):
            refused.append((order_id, ticker, price, previous.price))

    assert refused == [], f"the band would have refused real fills: {refused}"


def test_every_live_pending_order_is_inside_the_trigger_band():
    """Same control for TRIGGER_LEVEL_IMPLAUSIBLE, against the armed orders.

    Measured 2026-08-07: all live pending levels sit within [0.77, 2.06] of
    their ticker's close — two orders of magnitude clear of the band, and of
    the 95.7 the pence-stop incident produced.
    """
    today = date.today()
    offenders = []
    ratios = []
    for order in _pending_orders():
        trigger = order.get("trigger") or {}
        level = trigger.get("level")
        if not isinstance(level, (int, float)) or level <= 0:
            continue
        quote = latest_price(order["ticker"], today)
        if quote is None or quote.price <= 0:
            continue
        ratio = level / quote.price
        ratios.append(ratio)
        if ratio < TRIGGER_LEVEL_MIN or ratio > TRIGGER_LEVEL_MAX:
            offenders.append(
                (order.get("order_id"), order["ticker"], level, quote.price)
            )

    assert ratios, "no priced pending orders found — this test asserted nothing"
    assert offenders == [], f"live pending orders outside the trigger band: {offenders}"


@pytest.mark.parametrize(
    "level, price, expected",
    [
        (111.0, 1.16, True),  # incident #9: a stop authored in pence
        (1.04, 1.16, False),  # an ordinary 10% stop
        (2.06, 1.16, False),  # the widest ratio live today
    ],
)
def test_the_trigger_band_separates_the_incident_from_real_orders(
    level, price, expected
):
    """The band has to sit between the two populations, not merely above one."""
    ratio = level / price
    assert (ratio < TRIGGER_LEVEL_MIN or ratio > TRIGGER_LEVEL_MAX) is expected
