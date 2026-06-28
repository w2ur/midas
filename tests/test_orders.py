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
                order_id="ord_x",
                ts=datetime.now(timezone.utc),
                agent_id="satoshi",
                action="BUY",
                ticker="BTC-EUR",
                shares=0.0,
                reasoning="test",
                currency="EUR",
            )

    def test_shares_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="shares must be > 0"):
            Order(
                order_id="ord_x",
                ts=datetime.now(timezone.utc),
                agent_id="satoshi",
                action="BUY",
                ticker="BTC-EUR",
                shares=-1.0,
                reasoning="test",
                currency="EUR",
            )

    def test_invalid_action_rejected(self) -> None:
        with pytest.raises(ValueError, match="action must be"):
            Order(
                order_id="ord_x",
                ts=datetime.now(timezone.utc),
                agent_id="satoshi",
                action="SHORT",
                ticker="BTC-EUR",
                shares=0.01,
                reasoning="test",
                currency="EUR",
            )

    def test_buy_accepted(self) -> None:
        Order(
            order_id="ord_x",
            ts=datetime.now(timezone.utc),
            agent_id="satoshi",
            action="BUY",
            ticker="BTC-EUR",
            shares=0.01,
            reasoning="test",
            currency="EUR",
        )

    def test_sell_accepted(self) -> None:
        Order(
            order_id="ord_x",
            ts=datetime.now(timezone.utc),
            agent_id="satoshi",
            action="SELL",
            ticker="BTC-EUR",
            shares=0.01,
            reasoning="test",
            currency="EUR",
        )

    def test_shares_nan_rejected(self) -> None:
        with pytest.raises(ValueError, match="shares must be > 0"):
            Order(
                order_id="ord_x",
                ts=datetime.now(timezone.utc),
                agent_id="satoshi",
                action="BUY",
                ticker="BTC-EUR",
                shares=float("nan"),
                reasoning="test",
                currency="EUR",
            )


class TestFillValidation:
    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValueError, match="status must be"):
            Fill(
                order_id="ord_x",
                ts_filled=datetime.now(timezone.utc),
                status="pending",
                fill_price=None,
                fill_currency=None,
                notional_base=None,
                fees=None,
                reason=None,
            )

    def test_filled_status_accepted(self) -> None:
        Fill(
            order_id="ord_x",
            ts_filled=datetime.now(timezone.utc),
            status="filled",
            fill_price=100.0,
            fill_currency="EUR",
            notional_base=100.0,
            fees=0.0,
            reason=None,
        )

    def test_rejected_status_accepted(self) -> None:
        Fill(
            order_id="ord_x",
            ts_filled=datetime.now(timezone.utc),
            status="rejected",
            fill_price=None,
            fill_currency=None,
            notional_base=None,
            fees=None,
            reason="MAX_ORDERS_PER_DAY",
        )


class TestOutboxRoundTrip:
    def test_append_and_read(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.orders.OUTBOX_DIR", tmp_path)
        d = date(2026, 4, 17)
        order = Order(
            order_id="ord_2026-04-17_satoshi_001",
            ts=datetime(2026, 4, 17, 20, 2, 15, tzinfo=timezone.utc),
            agent_id="satoshi",
            action="BUY",
            ticker="BTC-EUR",
            shares=0.01,
            reasoning="dip",
            currency="EUR",
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
            append_order(
                d,
                Order(
                    order_id=make_order_id(d, "satoshi", i),
                    ts=datetime(2026, 4, 17, 20, 0, i, tzinfo=timezone.utc),
                    agent_id="satoshi",
                    action="BUY",
                    ticker="BTC-EUR",
                    shares=0.01,
                    reasoning=f"#{i}",
                    currency="EUR",
                ),
            )
        orders = read_outbox(d)
        assert [o.order_id[-3:] for o in orders] == ["001", "002", "003"]

    def test_ts_serializes_with_z_suffix(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.orders.OUTBOX_DIR", tmp_path)
        d = date(2026, 4, 17)
        order = Order(
            order_id="ord_x",
            ts=datetime(2026, 4, 17, 20, 2, 15, tzinfo=timezone.utc),
            agent_id="satoshi",
            action="BUY",
            ticker="BTC-EUR",
            shares=0.01,
            reasoning="test",
            currency="EUR",
        )
        append_order(d, order)
        raw = (tmp_path / "2026-04-17.jsonl").read_text()
        assert '"ts": "2026-04-17T20:02:15Z"' in raw

    def test_empty_when_file_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.orders.OUTBOX_DIR", tmp_path)
        assert read_outbox(date(2026, 4, 17)) == []

    def test_malformed_jsonl_raises_with_context(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr("engine.orders.OUTBOX_DIR", tmp_path)
        path = tmp_path / "2026-04-17.jsonl"
        path.write_text('{"broken": ')  # truncated JSON
        with pytest.raises(ValueError, match="Malformed JSON"):
            read_outbox(date(2026, 4, 17))


class TestInboxRoundTrip:
    def test_filled_and_rejected(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.orders.INBOX_DIR", tmp_path)
        d = date(2026, 4, 17)
        append_fill(
            d,
            Fill(
                order_id="ord_2026-04-17_satoshi_001",
                ts_filled=datetime(2026, 4, 17, 20, 2, 17, tzinfo=timezone.utc),
                status="filled",
                fill_price=64320.50,
                fill_currency="EUR",
                notional_base=643.20,
                fees=0.0,
                reason=None,
            ),
        )
        append_fill(
            d,
            Fill(
                order_id="ord_2026-04-17_yolo-sapiens-usd_003",
                ts_filled=datetime(2026, 4, 17, 20, 2, 18, tzinfo=timezone.utc),
                status="rejected",
                fill_price=None,
                fill_currency=None,
                notional_base=None,
                fees=None,
                reason="MAX_ORDERS_PER_DAY",
            ),
        )
        fills = read_inbox(d)
        assert len(fills) == 2
        assert fills[0].status == "filled"
        assert fills[1].reason == "MAX_ORDERS_PER_DAY"

    def test_empty_when_file_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.orders.INBOX_DIR", tmp_path)
        assert read_inbox(date(2026, 4, 17)) == []


class TestOrderTriggerField:
    def test_market_order_has_no_trigger(self) -> None:
        o = Order(
            order_id="ord_x",
            ts=datetime.now(timezone.utc),
            agent_id="satoshi",
            action="BUY",
            ticker="BTC-EUR",
            shares=0.01,
            reasoning="test",
            currency="EUR",
        )
        assert o.trigger is None
        assert o.expires is None

    def test_conditional_order_accepts_trigger_and_expires(self) -> None:
        o = Order(
            order_id="ord_x",
            ts=datetime.now(timezone.utc),
            agent_id="satoshi",
            action="SELL",
            ticker="BTC-EUR",
            shares=0.01,
            reasoning="trim at resistance",
            currency="EUR",
            trigger={"op": ">=", "level": 85000.0},
            expires="2026-06-17",
        )
        assert o.trigger == {"op": ">=", "level": 85000.0}
        assert o.expires == "2026-06-17"

    def test_trigger_must_be_dict(self) -> None:
        with pytest.raises(ValueError, match="trigger must be a dict"):
            Order(
                order_id="ord_x",
                ts=datetime.now(timezone.utc),
                agent_id="satoshi",
                action="SELL",
                ticker="BTC-EUR",
                shares=0.01,
                reasoning="test",
                currency="EUR",
                trigger="85000",  # type: ignore[arg-type]
            )

    def test_trigger_unknown_op_rejected(self) -> None:
        with pytest.raises(ValueError, match="trigger.op must be one of"):
            Order(
                order_id="ord_x",
                ts=datetime.now(timezone.utc),
                agent_id="satoshi",
                action="SELL",
                ticker="BTC-EUR",
                shares=0.01,
                reasoning="test",
                currency="EUR",
                trigger={"op": "between", "level": 85000.0},
            )

    def test_trigger_missing_level_rejected(self) -> None:
        with pytest.raises(ValueError, match="trigger.level must be a number"):
            Order(
                order_id="ord_x",
                ts=datetime.now(timezone.utc),
                agent_id="satoshi",
                action="SELL",
                ticker="BTC-EUR",
                shares=0.01,
                reasoning="test",
                currency="EUR",
                trigger={"op": ">="},
            )

    def test_expires_must_be_iso_date(self) -> None:
        with pytest.raises(ValueError, match="expires must be ISO date"):
            Order(
                order_id="ord_x",
                ts=datetime.now(timezone.utc),
                agent_id="satoshi",
                action="SELL",
                ticker="BTC-EUR",
                shares=0.01,
                reasoning="test",
                currency="EUR",
                trigger={"op": ">=", "level": 85000.0},
                expires="next month",
            )

    def test_serde_round_trip_with_trigger(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.orders.OUTBOX_DIR", tmp_path)
        d = date(2026, 5, 17)
        o = Order(
            order_id="ord_2026-05-17_satoshi_001",
            ts=datetime(2026, 5, 17, 20, 2, 0, tzinfo=timezone.utc),
            agent_id="satoshi",
            action="SELL",
            ticker="BTC-EUR",
            shares=0.01,
            reasoning="trim",
            currency="EUR",
            trigger={"op": ">=", "level": 85000.0},
            expires="2026-06-17",
        )
        append_order(d, o)
        back = read_outbox(d)
        assert back[0].trigger == {"op": ">=", "level": 85000.0}
        assert back[0].expires == "2026-06-17"

    def test_expires_without_trigger_rejected(self) -> None:
        with pytest.raises(ValueError, match="expires requires trigger"):
            Order(
                order_id="ord_x",
                ts=datetime.now(timezone.utc),
                agent_id="satoshi",
                action="BUY",
                ticker="BTC-EUR",
                shares=0.01,
                reasoning="test",
                currency="EUR",
                trigger=None,
                expires="2026-06-17",
            )

    def test_serde_round_trip_legacy_without_trigger(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """An outbox file written by old code (no trigger/expires keys) must still parse."""
        monkeypatch.setattr("engine.orders.OUTBOX_DIR", tmp_path)
        d = date(2026, 4, 17)
        (tmp_path / f"{d.isoformat()}.jsonl").write_text(
            '{"order_id":"ord_x","ts":"2026-04-17T20:02:15Z","agent_id":"satoshi",'
            '"action":"BUY","ticker":"BTC-EUR","shares":0.01,"reasoning":"dip","currency":"EUR"}\n'
        )
        back = read_outbox(d)
        assert back[0].trigger is None
        assert back[0].expires is None


class TestFillTriggerFiredField:
    def test_default_is_false(self) -> None:
        f = Fill(
            order_id="ord_x",
            ts_filled=datetime.now(timezone.utc),
            status="filled",
            fill_price=100.0,
            fill_currency="EUR",
            notional_base=100.0,
            fees=0.0,
            reason=None,
        )
        assert f.trigger_fired is False

    def test_can_be_true_for_triggered_fills(self) -> None:
        f = Fill(
            order_id="ord_x",
            ts_filled=datetime.now(timezone.utc),
            status="filled",
            fill_price=100.0,
            fill_currency="EUR",
            notional_base=100.0,
            fees=0.0,
            reason=None,
            trigger_fired=True,
        )
        assert f.trigger_fired is True

    def test_serde_round_trip(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.orders.INBOX_DIR", tmp_path)
        d = date(2026, 5, 17)
        append_fill(
            d,
            Fill(
                order_id="ord_x",
                ts_filled=datetime(2026, 5, 17, 14, 30, tzinfo=timezone.utc),
                status="filled",
                fill_price=85100.0,
                fill_currency="EUR",
                notional_base=851.0,
                fees=0.0,
                reason=None,
                trigger_fired=True,
            ),
        )
        back = read_inbox(d)
        assert back[0].trigger_fired is True

    def test_serde_round_trip_legacy_inbox(self, tmp_path: Path, monkeypatch) -> None:
        """An inbox file written by old code (no trigger_fired key) must still parse, defaulting to False."""
        monkeypatch.setattr("engine.orders.INBOX_DIR", tmp_path)
        d = date(2026, 4, 17)
        (tmp_path / f"{d.isoformat()}.jsonl").write_text(
            '{"order_id":"ord_x","ts_filled":"2026-04-17T20:02:17Z","status":"filled",'
            '"fill_price":64320.5,"fill_currency":"EUR","notional_base":643.2,"fees":0.0,"reason":null}\n'
        )
        back = read_inbox(d)
        assert back[0].trigger_fired is False


class TestFillExecutedShaField:
    """executed_sha — the HEAD commit the broker executed against (audit provenance)."""

    def _fill(self, **kw) -> Fill:
        base = dict(
            order_id="ord_x",
            ts_filled=datetime.now(timezone.utc),
            status="filled",
            fill_price=100.0,
            fill_currency="EUR",
            notional_base=100.0,
            fees=0.0,
            reason=None,
        )
        base.update(kw)
        return Fill(**base)

    def test_default_is_none(self) -> None:
        assert self._fill().executed_sha is None

    def test_omitted_from_dict_when_none(self) -> None:
        """A null SHA must not pollute the committed JSONL payload."""
        assert "executed_sha" not in self._fill().to_dict()

    def test_present_in_dict_when_set(self) -> None:
        d = self._fill(executed_sha="a" * 40).to_dict()
        assert d["executed_sha"] == "a" * 40

    def test_serde_round_trip(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.orders.INBOX_DIR", tmp_path)
        d = date(2026, 5, 17)
        append_fill(d, self._fill(executed_sha="deadbeef" + "0" * 32))
        back = read_inbox(d)
        assert back[0].executed_sha == "deadbeef" + "0" * 32

    def test_serde_round_trip_legacy_inbox(self, tmp_path: Path, monkeypatch) -> None:
        """An inbox file written before executed_sha existed must parse, defaulting to None."""
        monkeypatch.setattr("engine.orders.INBOX_DIR", tmp_path)
        d = date(2026, 4, 17)
        (tmp_path / f"{d.isoformat()}.jsonl").write_text(
            '{"order_id":"ord_x","ts_filled":"2026-04-17T20:02:17Z","status":"filled",'
            '"fill_price":64320.5,"fill_currency":"EUR","notional_base":643.2,"fees":0.0,"reason":null}\n'
        )
        back = read_inbox(d)
        assert back[0].executed_sha is None
