"""Convert bt's transactions DataFrame into the API's TradeEntry list.

The bt library returns one row per fill (positive quantity = buy, negative =
sell). We compute per-position P&L by matching each sell to the FIFO buy of
the same ticker, then return all rows sorted by descending |P&L| capped at N.
"""

from __future__ import annotations

from collections import defaultdict, deque

import pandas as pd

from backtester.schemas import TradeEntry


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
    """
    if transactions is None or transactions.empty:
        return []

    rows: list[TradeEntry] = []
    open_lots: dict[str, deque[tuple[float, float]]] = defaultdict(deque)

    for _, row in transactions.sort_values("Date").iterrows():
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
                    pnl=realised,
                )
            )

    rows.sort(key=lambda t: abs(t.pnl) if t.pnl is not None else 0.0, reverse=True)
    return rows[:n]
