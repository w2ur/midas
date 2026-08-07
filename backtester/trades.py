"""Convert bt's transactions DataFrame into the API's TradeEntry list.

The bt library returns one row per fill (positive quantity = buy, negative =
sell). We compute per-position P&L by matching each sell to the FIFO buy of
the same ticker, then return all rows sorted by descending |P&L| capped at N.

**A P&L here is denominated in the TICKER's currency, and a universe can span
several.** `stoxx600` covers eight (CHF, DKK, EUR, GBP, NOK, PLN, SEK, USD)
across 463 constituents and `ftse100` covers three, so ranking by `abs(pnl)`
compares a krona amount against a pound one as if they were the same unit —
systematically promoting whichever currency has the weakest unit into the
"top trades" list. Same class as the cross-currency sums fixed four times
elsewhere in this project.

Two things follow, and deliberately not a third:

- every `TradeEntry` now carries its `currency`, so a P&L is never an
  unlabelled number;
- `mixed_currency_warning` lets the API say the ranking is not comparable,
  rather than presenting it as though it were.

What is NOT done here is converting to a single reporting currency. That needs
a rate per trade date, the service has no FX layer, and the site's precedent
(W7.2) is to suppress a mixed-currency figure rather than invent one. It is a
decision for the backtester spin-out, recorded here so the gap is visible in
the response instead of only in a backlog.
"""

from __future__ import annotations

from collections import defaultdict, deque

import pandas as pd

from backtester.schemas import TradeEntry
from engine.quotes import ticker_currency


def extract_top_trades(
    transactions: pd.DataFrame | None,
    n: int = 20,
) -> list[TradeEntry]:
    """Extract top N trades by absolute P&L, using FIFO lot matching.

    Args:
        transactions: DataFrame from bt with columns [Date, Security, quantity, price],
                     or None if no trades occurred.
        n: Maximum number of trades to return (sorted by |P&L| descending).

    Returns:
        List of TradeEntry objects sorted by descending absolute P&L, capped at N.
        Buys have pnl=None; sells have realized P&L computed via FIFO matching.
        Each entry carries the ticker's own `currency` (None when unresolvable);
        see the module docstring on why the ranking is not cross-comparable.
    """
    if transactions is None or transactions.empty:
        return []

    rows: list[TradeEntry] = []
    open_lots: dict[str, deque[tuple[float, float]]] = defaultdict(deque)

    # bt produces a MultiIndex (Date, Security) DataFrame; unit tests use a flat
    # DataFrame with Date/Security as columns. Handle both shapes.
    has_multiindex = isinstance(transactions.index, pd.MultiIndex)
    if has_multiindex:
        sorted_txns = transactions.sort_index()
    else:
        sorted_txns = transactions.sort_values("Date")

    for idx, row in sorted_txns.iterrows():
        if has_multiindex:
            ts, security = idx
            date_str = pd.Timestamp(ts).date().isoformat()
            ticker = str(security)
        else:
            date_str = pd.Timestamp(row["Date"]).date().isoformat()
            ticker = str(row["Security"])
        quantity = float(row["quantity"])
        price = float(row["price"])

        if quantity > 0:
            open_lots[ticker].append((quantity, price))
            rows.append(
                TradeEntry(
                    date=date_str,
                    ticker=ticker,
                    side="buy",
                    quantity=quantity,
                    price=price,
                    currency=ticker_currency(ticker),
                    pnl=None,
                )
            )
        else:
            sell_qty = abs(quantity)
            realised = 0.0
            remaining = sell_qty
            while remaining > 0 and open_lots[ticker]:
                lot_qty, lot_price = open_lots[ticker][0]
                matched = min(lot_qty, remaining)
                realised += matched * (price - lot_price)
                if matched == lot_qty:
                    open_lots[ticker].popleft()
                else:
                    open_lots[ticker][0] = (lot_qty - matched, lot_price)
                remaining -= matched
            rows.append(
                TradeEntry(
                    date=date_str,
                    ticker=ticker,
                    side="sell",
                    quantity=sell_qty,
                    price=price,
                    currency=ticker_currency(ticker),
                    pnl=realised,
                )
            )

    rows.sort(key=lambda t: abs(t.pnl) if t.pnl is not None else 0.0, reverse=True)
    return rows[:n]


MIXED_CURRENCY_WARNING = (
    "MIXED_CURRENCY_TRADES: these trades are denominated in {currencies}, and "
    "the P&L ranking compares the raw amounts without converting them. Treat "
    "the ordering as indicative only; a P&L is comparable to another only "
    "within the same currency."
)


def mixed_currency_warning(trades: list[TradeEntry]) -> str | None:
    """Warn when a trade list spans more than one currency, else None.

    Only currencies that actually carry a realised P&L count: a buy-side row
    has `pnl=None` and contributes nothing to the ranking, so a universe whose
    foreign names were never sold does not need the warning.
    """
    currencies = sorted(
        {t.currency for t in trades if t.pnl is not None and t.currency}
    )
    if len(currencies) < 2:
        return None
    return MIXED_CURRENCY_WARNING.format(currencies=", ".join(currencies))
