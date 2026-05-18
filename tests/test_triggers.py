"""Tests for engine.triggers — pending I/O, cancel I/O, evaluation, expiry."""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from engine.orders import Order
from engine.triggers import (
    CancelRequest,
    append_cancel,
    delete_pending,
    list_pending,
    read_cancels,
    save_pending,
)


def _make_order(order_id: str = "ord_2026-05-17_satoshi_001", **overrides) -> Order:
    defaults = dict(
        order_id=order_id,
        ts=datetime(2026, 5, 17, 20, 2, 0, tzinfo=timezone.utc),
        agent_id="satoshi",
        action="SELL",
        ticker="BTC-EUR",
        shares=0.01,
        reasoning="trim at resistance",
        currency="EUR",
        trigger={"op": ">=", "level": 85000.0},
        expires="2026-06-17",
    )
    defaults.update(overrides)
    return Order(**defaults)


class TestPendingStorage:
    def test_save_and_list(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.triggers.PENDING_DIR", tmp_path)
        o = _make_order()
        save_pending(o)
        pending = list_pending()
        assert len(pending) == 1
        assert pending[0].order_id == o.order_id
        assert pending[0].trigger == {"op": ">=", "level": 85000.0}

    def test_save_overwrites_same_id(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.triggers.PENDING_DIR", tmp_path)
        save_pending(_make_order(trigger={"op": ">=", "level": 85000.0}))
        save_pending(_make_order(trigger={"op": ">=", "level": 90000.0}))  # same id
        pending = list_pending()
        assert len(pending) == 1
        assert pending[0].trigger["level"] == 90000.0

    def test_delete_removes_file(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.triggers.PENDING_DIR", tmp_path)
        o = _make_order()
        save_pending(o)
        assert delete_pending(o.order_id) is True
        assert list_pending() == []

    def test_delete_missing_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.triggers.PENDING_DIR", tmp_path)
        assert delete_pending("ord_does_not_exist") is False

    def test_list_empty_when_dir_empty(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.triggers.PENDING_DIR", tmp_path)
        assert list_pending() == []

    def test_list_empty_when_dir_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.triggers.PENDING_DIR", tmp_path / "nonexistent")
        assert list_pending() == []

    def test_list_skips_gitkeep_and_non_json(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.triggers.PENDING_DIR", tmp_path)
        (tmp_path / ".gitkeep").write_text("")
        (tmp_path / "README.md").write_text("not a pending order")
        save_pending(_make_order())
        assert len(list_pending()) == 1

    def test_save_requires_trigger(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.triggers.PENDING_DIR", tmp_path)
        market_order = _make_order(trigger=None, expires=None)
        with pytest.raises(ValueError, match="save_pending requires order.trigger"):
            save_pending(market_order)


class TestCancelStorage:
    def test_append_and_read(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.triggers.CANCELS_DIR", tmp_path)
        d = date(2026, 5, 17)
        c = CancelRequest(
            request_id="cnl_2026-05-17_satoshi_001",
            ts=datetime(2026, 5, 17, 20, 5, tzinfo=timezone.utc),
            agent_id="satoshi",
            target_order_id="ord_2026-05-10_satoshi_003",
            reasoning="thesis changed, no longer want to sell at 85k",
        )
        append_cancel(d, c)
        back = read_cancels(d)
        assert len(back) == 1
        assert back[0].target_order_id == "ord_2026-05-10_satoshi_003"
        assert back[0].ts.tzinfo is not None

    def test_empty_when_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.triggers.CANCELS_DIR", tmp_path)
        assert read_cancels(date(2026, 5, 17)) == []

    def test_read_cancels_skips_malformed_lines(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr("engine.triggers.CANCELS_DIR", tmp_path)
        d = date(2026, 5, 17)
        # Mix: one good line, one truncated JSON, one good line.
        path = tmp_path / f"{d.isoformat()}.jsonl"
        path.write_text(
            '{"request_id":"cnl_a","ts":"2026-05-17T20:05:00Z","agent_id":"satoshi",'
            '"target_order_id":"ord_a","reasoning":"ok"}\n'
            '{"request_id":"cnl_b","ts":"2026-05-17T20:06:0\n'  # truncated
            '{"request_id":"cnl_c","ts":"2026-05-17T20:07:00Z","agent_id":"satoshi",'
            '"target_order_id":"ord_c","reasoning":"ok"}\n'
        )
        cancels = read_cancels(d)
        assert [c.request_id for c in cancels] == ["cnl_a", "cnl_c"]
