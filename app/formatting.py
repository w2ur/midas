"""Display formatting for the Streamlit dashboard.

Separate from the pages so it can be unit-tested: importing a Streamlit page
executes `st.set_page_config` and friends at module level, so anything defined
inside one is effectively untestable.

Live-desk only — the dashboard is not part of the midas-core mirror.
"""

from __future__ import annotations

#: Symbols for the currencies this desk actually books in. Deliberately not
#: exhaustive: a currency without an entry renders its ISO code instead of
#: being forced into a nearby sign.
CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥"}


def format_money(value: float, currency: str | None) -> str:
    """Format an amount in its own currency — never a hardcoded symbol.

    The dashboard renders whichever book the selector points at, and the books
    are not all USD: the strategy page's cash line was a hardcoded `$` across
    ten books, seven of which are EUR.

    Three cases, and the third is the one that matters:

    - a currency in `CURRENCY_SYMBOLS` renders with its sign (`€1,234.56`);
    - one that is not (CHF) renders as a trailing ISO code (`1,234.56 CHF`),
      rather than borrowing a symbol that means something else;
    - a missing currency says so. An unlabelled number is exactly the
      ambiguity this function exists to remove, so falling back to a bare
      figure would defeat it — the same reasoning as the Manager prompt's
      currency labels (W7.3) and the site's suppressed mixed-currency weight
      column (W7.2).
    """
    if not currency:
        return f"{value:,.2f} (currency unknown)"
    symbol = CURRENCY_SYMBOLS.get(currency)
    return f"{symbol}{value:,.2f}" if symbol else f"{value:,.2f} {currency}"
