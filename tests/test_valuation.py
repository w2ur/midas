"""Tests for engine.valuation helpers."""

from engine.valuation import mtm_base_currency


def test_mtm_base_currency_cash_only() -> None:
    summary = {"cash": 1000.0, "currency": "EUR", "positions": []}
    assert mtm_base_currency(summary) == 1000.0
