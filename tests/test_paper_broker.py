"""Tests for engine.paper_broker — 9 safety rails + fill logic.

Each test uses the broker_env fixture to isolate filesystem state:
- OHLCV store, agent config dir, ticker currencies override, outbox, inbox,
  and PortfolioManager base dir all live under tmp_path.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from engine.orders import Fill, Order, append_order
from engine.portfolio import PortfolioManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def broker_env(tmp_path, monkeypatch):
    ohlcv = tmp_path / "ohlcv"; ohlcv.mkdir()
    config_dir = tmp_path / "config"; config_dir.mkdir()
    ticker_ccy_path = tmp_path / "ticker_currencies.json"
    outbox = tmp_path / "outbox"; outbox.mkdir()
    inbox = tmp_path / "inbox"; inbox.mkdir()
    pm_base = tmp_path / "portfolios"; pm_base.mkdir()
    monkeypatch.setattr("engine.paper_broker._OHLCV_STORE", ohlcv)
    monkeypatch.setattr("engine.paper_broker.AGENT_CONFIG_DIR", config_dir)
    monkeypatch.setattr("engine.paper_broker.TICKER_CURRENCIES_PATH", ticker_ccy_path)
    monkeypatch.setattr("engine.paper_broker._TICKER_CURRENCY_OVERRIDES", None)
    monkeypatch.setattr("engine.orders.OUTBOX_DIR", outbox)
    monkeypatch.setattr("engine.orders.INBOX_DIR", inbox)
    return {
        "ohlcv": ohlcv, "config_dir": config_dir, "ticker_ccy": ticker_ccy_path,
        "outbox": outbox, "inbox": inbox, "pm_base": pm_base,
    }


def _seed_ohlcv(ohlcv_dir: Path, ticker: str, rows: list[tuple[str, float]]) -> None:
    """rows: list of (iso_date, close)."""
    path = ohlcv_dir / f"{ticker}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for d, close in rows:
            f.write(json.dumps({"date": d, "close": close, "adj_close": close}) + "\n")


def _write_config(config_dir: Path, agent_id: str, **overrides) -> None:
    cfg = {
        "max_order_notional": 10_000.0,
        "max_orders_per_day": 10,
        "daily_drawdown_halt_pct": -50.0,
        "allowed_universe": [],
        "dry_run": False,
    }
    cfg.update(overrides)
    (config_dir / f"{agent_id}.json").write_text(json.dumps(cfg), encoding="utf-8")


def _init_portfolio(pm_base: Path, agent_id: str, cash: float = 10_000.0, currency: str = "USD") -> PortfolioManager:
    pm = PortfolioManager(pm_base)
    pm.initialize(agent_id, initial_capital=cash, currency=currency)
    return pm


def _make_order(order_id: str, agent_id: str, action: str, ticker: str, shares: float,
                currency: str = "USD") -> Order:
    return Order(
        order_id=order_id,
        ts=datetime(2026, 4, 17, 20, 0, 0, tzinfo=timezone.utc),
        agent_id=agent_id,
        action=action,
        ticker=ticker,
        shares=shares,
        reasoning="test",
        currency=currency,
    )


TRADE_DATE = date(2026, 4, 17)


# ---------------------------------------------------------------------------
# 1. Fills a valid BUY
# ---------------------------------------------------------------------------

def test_fills_valid_buy_and_updates_portfolio(broker_env):
    from engine.paper_broker import fill_day

    _seed_ohlcv(broker_env["ohlcv"], "VOO", [("2026-04-17", 500.0)])
    _write_config(broker_env["config_dir"], "agent1",
                  allowed_universe=["single-voo"], max_order_notional=10_000.0)
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=5000.0, currency="USD")

    order = _make_order("ord_001", "agent1", "BUY", "VOO", 5)
    append_order(TRADE_DATE, order)

    fills = fill_day(TRADE_DATE, pm)

    assert len(fills) == 1
    assert fills[0].status == "filled"
    assert fills[0].fill_price == 500.0
    assert fills[0].notional == 2500.0
    p = pm.load("agent1")
    assert p.cash == 5000.0 - 2500.0
    assert len(p.positions) == 1
    assert p.positions[0].ticker == "VOO"
    assert p.positions[0].shares == 5


# ---------------------------------------------------------------------------
# 2. Fills a valid SELL
# ---------------------------------------------------------------------------

def test_fills_valid_sell_and_updates_portfolio(broker_env):
    from engine.paper_broker import fill_day
    from engine.types import Trade

    _seed_ohlcv(broker_env["ohlcv"], "VOO", [("2026-04-17", 500.0)])
    _write_config(broker_env["config_dir"], "agent1", allowed_universe=["single-voo"])
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=1000.0)

    # Seed a position by applying a BUY trade directly through the PortfolioManager.
    buy = Trade(id="seed_buy", timestamp=datetime(2026, 4, 16, 20, 0, 0, tzinfo=timezone.utc),
                action="BUY", ticker="VOO", shares=5, price=400.0, total=2000.0,
                fees=0.0, reasoning="seed")
    pm.initialize("agent1", initial_capital=3000.0)  # already there; noop but ensures state
    # Reset portfolio cash to cover the seed buy.
    p = pm.load("agent1")
    p.cash = 3000.0
    (broker_env["pm_base"] / "agent1" / "portfolio.json").write_text(json.dumps(p.to_dict()), encoding="utf-8")
    pm.apply_trade("agent1", buy)

    p_before = pm.load("agent1")
    cash_before = p_before.cash

    sell = _make_order("ord_sell", "agent1", "SELL", "VOO", 2)
    append_order(TRADE_DATE, sell)

    fills = fill_day(TRADE_DATE, pm)

    assert len(fills) == 1
    assert fills[0].status == "filled"
    p_after = pm.load("agent1")
    assert p_after.cash == cash_before + 2 * 500.0
    # After selling 2 of 5, 3 remain.
    assert p_after.positions[0].shares == 3


# ---------------------------------------------------------------------------
# 3. Rejects malformed outbox (shares <= 0)
# ---------------------------------------------------------------------------

def test_rejects_invalid_shares_from_malformed_outbox(broker_env):
    from engine.paper_broker import fill_day

    _write_config(broker_env["config_dir"], "agent1")
    pm = _init_portfolio(broker_env["pm_base"], "agent1")

    # Hand-write a bad line (shares=0) — bypasses Order validation.
    bad = {
        "order_id": "ord_bad",
        "ts": "2026-04-17T20:00:00Z",
        "agent_id": "agent1",
        "action": "BUY",
        "ticker": "VOO",
        "shares": 0,
        "reasoning": "r",
        "currency": "USD",
    }
    outbox_path = broker_env["outbox"] / f"{TRADE_DATE.isoformat()}.jsonl"
    outbox_path.write_text(json.dumps(bad) + "\n", encoding="utf-8")

    fills = fill_day(TRADE_DATE, pm)
    assert len(fills) == 1
    assert fills[0].status == "rejected"
    assert fills[0].reason == "INVALID_SHARES"


# ---------------------------------------------------------------------------
# 4. Rejects corrupted JSON line
# ---------------------------------------------------------------------------

def test_rejects_corrupted_json_outbox_line(broker_env):
    from engine.paper_broker import fill_day

    _write_config(broker_env["config_dir"], "agent1")
    pm = _init_portfolio(broker_env["pm_base"], "agent1")

    outbox_path = broker_env["outbox"] / f"{TRADE_DATE.isoformat()}.jsonl"
    outbox_path.write_text("this is not json at all\n", encoding="utf-8")

    fills = fill_day(TRADE_DATE, pm)
    assert len(fills) == 1
    assert fills[0].status == "rejected"
    assert fills[0].reason == "INVALID_SHARES"


# ---------------------------------------------------------------------------
# 5. Rejects when over max_orders_per_day
# ---------------------------------------------------------------------------

def test_rejects_when_over_max_orders_per_day(broker_env):
    from engine.paper_broker import fill_day

    _seed_ohlcv(broker_env["ohlcv"], "VOO", [("2026-04-17", 500.0)])
    _write_config(broker_env["config_dir"], "agent1",
                  allowed_universe=["single-voo"], max_orders_per_day=1)
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=20_000.0)

    append_order(TRADE_DATE, _make_order("ord_1", "agent1", "BUY", "VOO", 1))
    append_order(TRADE_DATE, _make_order("ord_2", "agent1", "BUY", "VOO", 1))

    fills = fill_day(TRADE_DATE, pm)
    assert len(fills) == 2
    statuses = {f.order_id: (f.status, f.reason) for f in fills}
    assert statuses["ord_1"][0] == "filled"
    assert statuses["ord_2"] == ("rejected", "MAX_ORDERS_PER_DAY")


# ---------------------------------------------------------------------------
# 6. Rejects when notional exceeds cap
# ---------------------------------------------------------------------------

def test_rejects_when_notional_exceeds_cap(broker_env):
    from engine.paper_broker import fill_day

    _seed_ohlcv(broker_env["ohlcv"], "VOO", [("2026-04-17", 500.0)])
    _write_config(broker_env["config_dir"], "agent1",
                  allowed_universe=["single-voo"], max_order_notional=100.0)
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=10_000.0)

    # 1 share × 500 = 500 > 100 cap
    append_order(TRADE_DATE, _make_order("ord_big", "agent1", "BUY", "VOO", 1))

    fills = fill_day(TRADE_DATE, pm)
    assert fills[0].status == "rejected"
    assert fills[0].reason == "MAX_ORDER_NOTIONAL"


# ---------------------------------------------------------------------------
# 7. Rejects when cash insufficient
# ---------------------------------------------------------------------------

def test_rejects_when_cash_insufficient(broker_env):
    from engine.paper_broker import fill_day

    _seed_ohlcv(broker_env["ohlcv"], "VOO", [("2026-04-17", 500.0)])
    _write_config(broker_env["config_dir"], "agent1", allowed_universe=["single-voo"])
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=100.0)  # too little

    append_order(TRADE_DATE, _make_order("ord_pricy", "agent1", "BUY", "VOO", 1))

    fills = fill_day(TRADE_DATE, pm)
    assert fills[0].status == "rejected"
    assert fills[0].reason == "INSUFFICIENT_CASH"


# ---------------------------------------------------------------------------
# 8. Rejects ticker outside universe
# ---------------------------------------------------------------------------

def test_rejects_when_ticker_outside_universe(broker_env):
    from engine.paper_broker import fill_day

    _seed_ohlcv(broker_env["ohlcv"], "MSFT", [("2026-04-17", 400.0)])
    _write_config(broker_env["config_dir"], "agent1", allowed_universe=["single-voo"])
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=10_000.0)

    append_order(TRADE_DATE, _make_order("ord_off", "agent1", "BUY", "MSFT", 1))

    fills = fill_day(TRADE_DATE, pm)
    assert fills[0].status == "rejected"
    assert fills[0].reason == "TICKER_NOT_IN_UNIVERSE"


# ---------------------------------------------------------------------------
# 9. Rejects when no price data
# ---------------------------------------------------------------------------

def test_rejects_when_no_price_data(broker_env):
    from engine.paper_broker import fill_day

    # Note: allow_universe empty → no universe check. No OHLCV seeded.
    _write_config(broker_env["config_dir"], "agent1", allowed_universe=[])
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=10_000.0)

    append_order(TRADE_DATE, _make_order("ord_noprice", "agent1", "BUY", "UNKNOWN", 1))

    fills = fill_day(TRADE_DATE, pm)
    assert fills[0].status == "rejected"
    assert fills[0].reason == "NO_PRICE_DATA"


# ---------------------------------------------------------------------------
# 10. Uses latest close on-or-before trade date
# ---------------------------------------------------------------------------

def test_uses_latest_close_on_or_before_when_today_missing(broker_env):
    from engine.paper_broker import fill_day

    # Store has 2026-04-16 but not 2026-04-17.
    _seed_ohlcv(broker_env["ohlcv"], "VOO", [("2026-04-16", 499.0)])
    _write_config(broker_env["config_dir"], "agent1", allowed_universe=["single-voo"])
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=10_000.0)

    append_order(TRADE_DATE, _make_order("ord_staleprice", "agent1", "BUY", "VOO", 1))

    fills = fill_day(TRADE_DATE, pm)
    assert fills[0].status == "filled"
    assert fills[0].fill_price == 499.0


# ---------------------------------------------------------------------------
# 11. Rejects SELL with no position
# ---------------------------------------------------------------------------

def test_rejects_sell_when_no_position(broker_env):
    from engine.paper_broker import fill_day

    _seed_ohlcv(broker_env["ohlcv"], "VOO", [("2026-04-17", 500.0)])
    _write_config(broker_env["config_dir"], "agent1", allowed_universe=["single-voo"])
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=10_000.0)

    append_order(TRADE_DATE, _make_order("ord_sellnothing", "agent1", "SELL", "VOO", 1))

    fills = fill_day(TRADE_DATE, pm)
    assert fills[0].status == "rejected"
    assert fills[0].reason == "NO_POSITION_TO_SELL"


# ---------------------------------------------------------------------------
# 12. Rejects SELL with insufficient shares
# ---------------------------------------------------------------------------

def test_rejects_sell_when_insufficient_shares(broker_env):
    from engine.paper_broker import fill_day
    from engine.types import Trade

    _seed_ohlcv(broker_env["ohlcv"], "VOO", [("2026-04-17", 500.0)])
    _write_config(broker_env["config_dir"], "agent1", allowed_universe=["single-voo"])
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=10_000.0)

    # Seed holding 5 shares via apply_trade.
    seed = Trade(id="seed", timestamp=datetime(2026, 4, 16, tzinfo=timezone.utc),
                 action="BUY", ticker="VOO", shares=5, price=400.0, total=2000.0,
                 fees=0.0, reasoning="seed")
    pm.apply_trade("agent1", seed)

    append_order(TRADE_DATE, _make_order("ord_over", "agent1", "SELL", "VOO", 10))

    fills = fill_day(TRADE_DATE, pm)
    assert fills[0].status == "rejected"
    assert fills[0].reason == "INSUFFICIENT_SHARES"


# ---------------------------------------------------------------------------
# 13. Drawdown halt rejects all orders
# ---------------------------------------------------------------------------

def test_rejects_all_orders_when_drawdown_halt_triggered(broker_env):
    from engine.paper_broker import fill_day

    _seed_ohlcv(broker_env["ohlcv"], "VOO", [("2026-04-17", 500.0)])
    _write_config(broker_env["config_dir"], "agent1",
                  allowed_universe=["single-voo"], daily_drawdown_halt_pct=-5.0)
    # Portfolio is tiny (cash=100) but previous snapshot claimed 10_000 → big drawdown.
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=100.0)
    pm.add_snapshot("agent1", date(2026, 4, 16), portfolio_value=10_000.0,
                    cash=10_000.0, positions_value=0.0, benchmarks={})

    append_order(TRADE_DATE, _make_order("ord_a", "agent1", "BUY", "VOO", 1))
    append_order(TRADE_DATE, _make_order("ord_b", "agent1", "BUY", "VOO", 1))

    fills = fill_day(TRADE_DATE, pm)
    assert len(fills) == 2
    assert all(f.status == "rejected" for f in fills)
    assert all(f.reason == "DAILY_DRAWDOWN_HALT" for f in fills)


# ---------------------------------------------------------------------------
# 14. Ticker currency override takes precedence
# ---------------------------------------------------------------------------

def test_ticker_currency_override_takes_precedence_over_heuristic(broker_env):
    from engine.paper_broker import fill_day

    # MSFT default would be USD. Override says EUR.
    broker_env["ticker_ccy"].write_text(json.dumps({"MSFT": "EUR"}), encoding="utf-8")
    _seed_ohlcv(broker_env["ohlcv"], "MSFT", [("2026-04-17", 100.0)])
    _write_config(broker_env["config_dir"], "agent1", allowed_universe=[])  # no allowlist
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=10_000.0, currency="EUR")

    append_order(TRADE_DATE, _make_order("ord_eur", "agent1", "BUY", "MSFT", 1))

    fills = fill_day(TRADE_DATE, pm)
    assert fills[0].status == "filled"
    assert fills[0].fill_currency == "EUR"
    # Because ticker_ccy == base_ccy, notional == notional_native, no FX conversion.
    assert fills[0].notional == 100.0


# ---------------------------------------------------------------------------
# 15. apply_trade failure → APPLY_TRADE_FAILED, loop continues
# ---------------------------------------------------------------------------

def test_apply_trade_failure_rejects_cleanly_and_continues_loop(broker_env, monkeypatch):
    from engine.paper_broker import fill_day

    _seed_ohlcv(broker_env["ohlcv"], "VOO", [("2026-04-17", 500.0)])
    _write_config(broker_env["config_dir"], "agent1", allowed_universe=["single-voo"])
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=10_000.0)

    original_apply = PortfolioManager.apply_trade
    call_state = {"n": 0}

    def flaky_apply(self, sid, trade):
        call_state["n"] += 1
        if call_state["n"] == 1:
            raise ValueError("simulated failure")
        return original_apply(self, sid, trade)

    monkeypatch.setattr(PortfolioManager, "apply_trade", flaky_apply)

    append_order(TRADE_DATE, _make_order("ord_boom", "agent1", "BUY", "VOO", 1))
    append_order(TRADE_DATE, _make_order("ord_ok", "agent1", "BUY", "VOO", 1))

    fills = fill_day(TRADE_DATE, pm)
    assert len(fills) == 2
    by_id = {f.order_id: f for f in fills}
    assert by_id["ord_boom"].status == "rejected"
    assert by_id["ord_boom"].reason == "APPLY_TRADE_FAILED"
    assert by_id["ord_ok"].status == "filled"


# ---------------------------------------------------------------------------
# 16. dry_run mode
# ---------------------------------------------------------------------------

def test_dry_run_fills_inbox_but_does_not_mutate_portfolio(broker_env):
    from engine.paper_broker import fill_day

    _seed_ohlcv(broker_env["ohlcv"], "VOO", [("2026-04-17", 500.0)])
    _write_config(broker_env["config_dir"], "agent1",
                  allowed_universe=["single-voo"], dry_run=True)
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=10_000.0)
    cash_before = pm.load("agent1").cash

    append_order(TRADE_DATE, _make_order("ord_dry", "agent1", "BUY", "VOO", 1))

    fills = fill_day(TRADE_DATE, pm)
    assert fills[0].status == "filled"
    p_after = pm.load("agent1")
    assert p_after.cash == cash_before
    assert len(p_after.positions) == 0
