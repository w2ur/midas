import pandas as pd

from backtester.trades import extract_top_trades


def _make_transactions() -> pd.DataFrame:
    """Synthetic transactions in the shape bt produces."""
    return pd.DataFrame(
        [
            {"Date": "2024-01-02", "Security": "AAPL", "quantity": 10, "price": 150.0},
            {"Date": "2024-02-15", "Security": "AAPL", "quantity": -10, "price": 200.0},
            {"Date": "2024-03-01", "Security": "TSLA", "quantity": 5, "price": 100.0},
            {"Date": "2024-04-10", "Security": "TSLA", "quantity": -5, "price": 80.0},
        ]
    )


def test_extract_top_trades_returns_closed_trades_only():
    transactions = _make_transactions()
    trades = extract_top_trades(transactions, n=10)
    assert len(trades) == 4


def test_extract_top_trades_sort_by_abs_pnl():
    transactions = _make_transactions()
    trades = extract_top_trades(transactions, n=2)
    assert trades[0].ticker == "AAPL"
    assert trades[0].side == "sell"
    assert trades[0].pnl == 500.0


def test_extract_top_trades_handles_none_transactions():
    assert extract_top_trades(None, n=10) == []


def test_extract_top_trades_handles_empty_transactions():
    empty = pd.DataFrame(columns=["Date", "Security", "quantity", "price"])
    assert extract_top_trades(empty, n=10) == []
