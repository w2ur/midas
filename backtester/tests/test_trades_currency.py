"""A trade's P&L is denominated in the ticker's currency, and universes mix them.

`extract_top_trades` ranks by `abs(pnl)` across every ticker in the run.
`stoxx600` spans eight currencies (CHF, DKK, EUR, GBP, NOK, PLN, SEK, USD)
across 463 constituents and `ftse100` spans three, so that ranking compared a
krona amount against a pound one as if they were the same unit — systematically
promoting whichever currency has the weakest unit into the "top trades" list.
Fifth appearance of the cross-currency-sum class in this project.

The response now labels each entry and warns when the ranking is not
comparable. It does NOT convert: that needs a rate per trade date, the service
has no FX layer, and the site's precedent (W7.2) is to suppress a
mixed-currency figure rather than invent one.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtester.trades import (
    MIXED_CURRENCY_WARNING,
    extract_top_trades,
    mixed_currency_warning,
)


def _txns(rows: list[tuple[str, str, float, float]]) -> pd.DataFrame:
    """rows: (date, ticker, quantity, price) — positive qty buys, negative sells."""
    return pd.DataFrame(
        [
            {"Date": d, "Security": t, "quantity": q, "price": p}
            for d, t, q, p in rows
        ]
    )


class TestTradesCarryTheirCurrency:
    def test_a_sterling_ticker_is_labelled_gbp(self):
        trades = extract_top_trades(
            _txns(
                [
                    ("2026-01-02", "LLOY.L", 100.0, 1.0),
                    ("2026-01-05", "LLOY.L", -100.0, 1.2),
                ]
            )
        )
        assert {t.currency for t in trades} == {"GBP"}

    def test_a_us_ticker_is_labelled_usd(self):
        trades = extract_top_trades(
            _txns(
                [
                    ("2026-01-02", "AAPL", 10.0, 100.0),
                    ("2026-01-05", "AAPL", -10.0, 120.0),
                ]
            )
        )
        assert {t.currency for t in trades} == {"USD"}

    def test_an_unresolvable_ticker_is_none_not_a_guess(self):
        trades = extract_top_trades(
            _txns([("2026-01-02", "ZZZZ.QQ", 1.0, 5.0)])
        )
        assert trades[0].currency is None

    def test_buys_are_labelled_too_even_though_they_carry_no_pnl(self):
        trades = extract_top_trades(_txns([("2026-01-02", "AAPL", 10.0, 100.0)]))
        assert trades[0].pnl is None
        assert trades[0].currency == "USD"


class TestMixedCurrencyWarning:
    def test_a_single_currency_run_is_not_warned_about(self):
        trades = extract_top_trades(
            _txns(
                [
                    ("2026-01-02", "AAPL", 10.0, 100.0),
                    ("2026-01-05", "AAPL", -10.0, 120.0),
                ]
            )
        )
        assert mixed_currency_warning(trades) is None

    def test_two_currencies_with_realised_pnl_are_warned_about(self):
        trades = extract_top_trades(
            _txns(
                [
                    ("2026-01-02", "AAPL", 10.0, 100.0),
                    ("2026-01-03", "LLOY.L", 100.0, 1.0),
                    ("2026-01-05", "AAPL", -10.0, 120.0),
                    ("2026-01-06", "LLOY.L", -100.0, 1.2),
                ]
            )
        )
        warning = mixed_currency_warning(trades)
        assert warning is not None
        assert "MIXED_CURRENCY_TRADES" in warning
        assert "GBP" in warning and "USD" in warning

    def test_a_foreign_name_never_sold_does_not_trigger_the_warning(self):
        """Only realised P&L enters the ranking, so only it can distort it."""
        trades = extract_top_trades(
            _txns(
                [
                    ("2026-01-02", "AAPL", 10.0, 100.0),
                    ("2026-01-03", "LLOY.L", 100.0, 1.0),  # bought, never sold
                    ("2026-01-05", "AAPL", -10.0, 120.0),
                ]
            )
        )
        assert mixed_currency_warning(trades) is None

    def test_empty_and_none_inputs_are_silent(self):
        assert mixed_currency_warning([]) is None
        assert extract_top_trades(None) == []
        assert mixed_currency_warning(extract_top_trades(None)) is None

    def test_the_warning_names_every_currency_present(self):
        trades = extract_top_trades(
            _txns(
                [
                    ("2026-01-02", "AAPL", 10.0, 100.0),
                    ("2026-01-03", "LLOY.L", 100.0, 1.0),
                    ("2026-01-04", "SIKA.SW", 5.0, 200.0),
                    ("2026-01-05", "AAPL", -10.0, 120.0),
                    ("2026-01-06", "LLOY.L", -100.0, 1.2),
                    ("2026-01-07", "SIKA.SW", -5.0, 220.0),
                ]
            )
        )
        warning = mixed_currency_warning(trades)
        assert warning is not None
        for code in ("CHF", "GBP", "USD"):
            assert code in warning

    def test_the_ranking_really_is_distorted_without_conversion(self):
        """The control: shows the defect the warning exists to disclose.

        A GBP trade earning 1,000 outranks nothing here — a nominally larger
        SEK-scale number wins the sort despite being worth far less. If a
        future change starts converting, this test fails and the warning
        should be removed rather than left lying.
        """
        trades = extract_top_trades(
            _txns(
                [
                    # +10,000 in a weak unit
                    ("2026-01-02", "VOLV-B.ST", 1000.0, 100.0),
                    ("2026-01-05", "VOLV-B.ST", -1000.0, 110.0),
                    # +1,000 in a strong one
                    ("2026-01-02", "LLOY.L", 1000.0, 10.0),
                    ("2026-01-05", "LLOY.L", -1000.0, 11.0),
                ]
            )
        )
        realised = [t for t in trades if t.pnl is not None]
        assert realised[0].ticker == "VOLV-B.ST", (
            "expected the raw-amount sort to promote the weak-unit trade"
        )
        assert mixed_currency_warning(trades) is not None


class TestWarningTemplate:
    def test_the_template_has_the_placeholder_it_is_formatted_with(self):
        assert "{currencies}" in MIXED_CURRENCY_WARNING
        assert MIXED_CURRENCY_WARNING.format(currencies="EUR, USD").count("{") == 0
