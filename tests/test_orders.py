"""Tests for the orders module."""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from engine.orders import (
    Fill,
    Order,
    append_fill,
    append_order,
    make_order_id,
    read_inbox,
    read_outbox,
)


class TestOrderIdGeneration:
    def test_deterministic_sequential(self) -> None:
        d = date(2026, 4, 17)
        assert make_order_id(d, "satoshi", 1) == "ord_2026-04-17_satoshi_001"
        assert make_order_id(d, "satoshi", 42) == "ord_2026-04-17_satoshi_042"


class TestOrderValidation:
    def test_shares_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="shares must be > 0"):
            Order(
                order_id="ord_x", ts=datetime.now(timezone.utc),
                agent_id="satoshi", action="BUY", ticker="BTC-EUR",
                shares=0.0, reasoning="test", currency="EUR",
            )

    def test_shares_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="shares must be > 0"):
            Order(
                order_id="ord_x", ts=datetime.now(timezone.utc),
                agent_id="satoshi", action="BUY", ticker="BTC-EUR",
                shares=-1.0, reasoning="test", currency="EUR",
            )

    def test_invalid_action_rejected(self) -> None:
        with pytest.raises(ValueError, match="action must be"):
            Order(
                order_id="ord_x", ts=datetime.now(timezone.utc),
                agent_id="satoshi", action="SHORT", ticker="BTC-EUR",
                shares=0.01, reasoning="test", currency="EUR",
            )

    def test_buy_accepted(self) -> None:
        Order(
            order_id="ord_x", ts=datetime.now(timezone.utc),
            agent_id="satoshi", action="BUY", ticker="BTC-EUR",
            shares=0.01, reasoning="test", currency="EUR",
        )

    def test_sell_accepted(self) -> None:
        Order(
            order_id="ord_x", ts=datetime.now(timezone.utc),
            agent_id="satoshi", action="SELL", ticker="BTC-EUR",
            shares=0.01, reasoning="test", currency="EUR",
        )


class TestOutboxRoundTrip:
    def test_append_and_read(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.orders.OUTBOX_DIR", tmp_path)
        d = date(2026, 4, 17)
        order = Order(
            order_id="ord_2026-04-17_satoshi_001",
            ts=datetime(2026, 4, 17, 20, 2, 15, tzinfo=timezone.utc),
            agent_id="satoshi", action="BUY", ticker="BTC-EUR",
            shares=0.01, reasoning="dip", currency="EUR",
        )
        append_order(d, order)
        read_back = read_outbox(d)
        assert len(read_back) == 1
        assert read_back[0].order_id == order.order_id
        assert read_back[0].shares == 0.01
        assert read_back[0].ts.tzinfo is not None

    def test_multiple_append_preserves_order(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.orders.OUTBOX_DIR", tmp_path)
        d = date(2026, 4, 17)
        for i in range(1, 4):
            append_order(d, Order(
                order_id=make_order_id(d, "satoshi", i),
                ts=datetime(2026, 4, 17, 20, 0, i, tzinfo=timezone.utc),
                agent_id="satoshi", action="BUY", ticker="BTC-EUR",
                shares=0.01, reasoning=f"#{i}", currency="EUR",
            ))
        orders = read_outbox(d)
        assert [o.order_id[-3:] for o in orders] == ["001", "002", "003"]

    def test_ts_serializes_with_z_suffix(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.orders.OUTBOX_DIR", tmp_path)
        d = date(2026, 4, 17)
        order = Order(
            order_id="ord_x",
            ts=datetime(2026, 4, 17, 20, 2, 15, tzinfo=timezone.utc),
            agent_id="satoshi", action="BUY", ticker="BTC-EUR",
            shares=0.01, reasoning="test", currency="EUR",
        )
        append_order(d, order)
        raw = (tmp_path / "2026-04-17.jsonl").read_text()
        assert '"ts": "2026-04-17T20:02:15Z"' in raw

    def test_empty_when_file_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.orders.OUTBOX_DIR", tmp_path)
        assert read_outbox(date(2026, 4, 17)) == []


class TestInboxRoundTrip:
    def test_filled_and_rejected(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.orders.INBOX_DIR", tmp_path)
        d = date(2026, 4, 17)
        append_fill(d, Fill(
            order_id="ord_2026-04-17_satoshi_001",
            ts_filled=datetime(2026, 4, 17, 20, 2, 17, tzinfo=timezone.utc),
            status="filled",
            fill_price=64320.50,
            fill_currency="EUR",
            notional=643.20,
            fees=0.0,
            reason=None,
        ))
        append_fill(d, Fill(
            order_id="ord_2026-04-17_yolo-sapiens-usd_003",
            ts_filled=datetime(2026, 4, 17, 20, 2, 18, tzinfo=timezone.utc),
            status="rejected",
            fill_price=None,
            fill_currency=None,
            notional=None,
            fees=None,
            reason="MAX_ORDERS_PER_DAY",
        ))
        fills = read_inbox(d)
        assert len(fills) == 2
        assert fills[0].status == "filled"
        assert fills[1].reason == "MAX_ORDERS_PER_DAY"

    def test_empty_when_file_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.orders.INBOX_DIR", tmp_path)
        assert read_inbox(date(2026, 4, 17)) == []
