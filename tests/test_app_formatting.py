"""The dashboard renders money in the book's own currency.

`app/pages/02_strategy.py` hardcoded `$` on the cash line while rendering
whichever book the selector points at — seven of the ten are EUR. Same family
as the Manager prompt's unlabelled position values (W7.3) and the site's
mixed-currency weight column (W7.2): a number whose currency is implied by
nothing.

Live-desk only; the Streamlit dashboard is not part of the midas-core mirror.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.formatting import CURRENCY_SYMBOLS, format_money


class TestFormatMoney:
    def test_eur_book_does_not_render_a_dollar_sign(self):
        """The actual defect: a EUR book showing $."""
        rendered = format_money(1234.5, "EUR")
        assert rendered == "€1,234.50"
        assert "$" not in rendered

    def test_usd_book_still_renders_a_dollar_sign(self):
        assert format_money(1234.5, "USD") == "$1,234.50"

    @pytest.mark.parametrize("currency", sorted(CURRENCY_SYMBOLS))
    def test_every_mapped_currency_renders_its_own_symbol(self, currency):
        assert format_money(1.0, currency).startswith(CURRENCY_SYMBOLS[currency])

    def test_unmapped_currency_gets_an_iso_code_not_a_borrowed_symbol(self):
        """CHF has no sign here — `world` holds SIKA.SW, so this is live."""
        rendered = format_money(187.25, "CHF")
        assert rendered == "187.25 CHF"
        for symbol in CURRENCY_SYMBOLS.values():
            assert symbol not in rendered

    @pytest.mark.parametrize("missing", [None, ""])
    def test_missing_currency_says_so_rather_than_showing_a_bare_number(self, missing):
        """A bare figure is the ambiguity this function exists to remove.

        Falling back to an unlabelled number would defeat the purpose, so the
        absence is stated instead.
        """
        assert format_money(10.0, missing) == "10.00 (currency unknown)"

    def test_thousands_separator_survives_every_branch(self):
        assert "1,000,000" in format_money(1_000_000, "EUR")
        assert "1,000,000" in format_money(1_000_000, "CHF")
        assert "1,000,000" in format_money(1_000_000, None)

    def test_negative_amounts_keep_their_sign(self):
        assert format_money(-5.0, "EUR") == "€-5.00"


@pytest.mark.live_cast
class TestAgainstTheLiveBooks:
    """The committed books are what the page actually renders."""

    def test_every_book_declares_a_currency(self):
        root = Path(__file__).resolve().parents[1] / "data" / "portfolios"
        for portfolio in sorted(root.glob("*/portfolio.json")):
            book = json.loads(portfolio.read_text(encoding="utf-8"))
            assert book.get("currency"), (
                f"{portfolio.parent.name} has no currency, so its cash line "
                "would render as '(currency unknown)'"
            )

    def test_a_mixed_currency_book_exists_so_this_is_not_hypothetical(self):
        """`world` is EUR and holds a CHF position.

        Without this, the unmapped-currency path above is a test of a case the
        desk never produces. It does produce it.
        """
        from engine.quotes import ticker_currency

        root = Path(__file__).resolve().parents[1] / "data" / "portfolios"
        book = json.loads((root / "world" / "portfolio.json").read_text("utf-8"))
        held = {ticker_currency(p["ticker"]) for p in book["positions"]}
        assert len(held - {None}) > 1, (
            f"expected `world` to hold more than one currency, got {held}"
        )
