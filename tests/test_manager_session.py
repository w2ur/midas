"""Tests for the paper Manager session wiring (Task C5a) — TDD.

Covers:
- manager_decision_to_orders: BUY/SELL → Orders (shares=size_eur/price), HOLD
  skipped, no-price skipped, agent_id "the-manager", deterministic order_ids.
- step_apply_manager_decision: HOLD/empty → no orders, portfolio unchanged, but
  manager-review STILL written (audit every day).
- valid decision → manager-outbox written, filled into the-manager portfolio with
  fees, manager-inbox written, PUBLIC inbox untouched.
- fill_day default path unchanged by the parameterization (focused test).
- idempotency on the manager channel (re-run → no double-fill).
- exclusion: the-manager not in AGENT_POST_TIMES; output bundle excludes it;
  public inbox carries no the-manager order_ids.

All filesystem state is redirected to tmp_path — no real data dirs are written
(the build_tax_shadow leak taught this discipline).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from engine.manager_decision import ManagerDecision, ManagerPosition
from engine.manager_orders import manager_decision_to_orders
from engine.orders import Order, read_inbox, read_outbox
from engine.portfolio import PortfolioManager


TRADE_DATE = date(2026, 6, 1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pos(
    ticker: str,
    action: str,
    size_eur: int,
    reasoning: str = "conviction call",
) -> ManagerPosition:
    return ManagerPosition(
        ticker=ticker,
        action=action,
        size_eur=size_eur,
        entry_guidance="",
        stop_loss=None,
        reasoning=reasoning,
    )


def _price_lookup(prices: dict[str, float]):
    def lookup(ticker: str) -> float | None:
        return prices.get(ticker)

    return lookup


# ---------------------------------------------------------------------------
# manager_decision_to_orders — pure conversion
# ---------------------------------------------------------------------------


def test_decision_to_orders_buy_and_sell():
    decision = ManagerDecision(
        positions=[
            _pos("AAPL", "BUY", 400),
            _pos("BTC-EUR", "SELL", 200),
        ],
        conviction=8,
        hold_reasoning="",
    )
    lookup = _price_lookup({"AAPL": 200.0, "BTC-EUR": 50_000.0})

    orders = manager_decision_to_orders(decision, TRADE_DATE, lookup)

    assert len(orders) == 2
    by_ticker = {o.ticker: o for o in orders}

    buy = by_ticker["AAPL"]
    assert buy.action == "BUY"
    assert buy.agent_id == "the-manager"
    assert buy.shares == pytest.approx(400 / 200.0)
    assert buy.reasoning == "conviction call"

    sell = by_ticker["BTC-EUR"]
    assert sell.action == "SELL"
    assert sell.shares == pytest.approx(200 / 50_000.0)


def test_decision_to_orders_skips_hold():
    decision = ManagerDecision(
        positions=[
            _pos("AAPL", "BUY", 400),
            _pos("MSFT", "HOLD", 0),
        ],
        conviction=8,
        hold_reasoning="",
    )
    orders = manager_decision_to_orders(
        decision, TRADE_DATE, _price_lookup({"AAPL": 200.0, "MSFT": 300.0})
    )
    assert [o.ticker for o in orders] == ["AAPL"]


def test_decision_to_orders_skips_no_price():
    decision = ManagerDecision(
        positions=[
            _pos("AAPL", "BUY", 400),
            _pos("UNKNOWN", "BUY", 400),
        ],
        conviction=8,
        hold_reasoning="",
    )
    # UNKNOWN has no store price → skipped (logged).
    orders = manager_decision_to_orders(
        decision, TRADE_DATE, _price_lookup({"AAPL": 200.0})
    )
    assert [o.ticker for o in orders] == ["AAPL"]


def test_decision_to_orders_deterministic_ids():
    decision = ManagerDecision(
        positions=[_pos("AAPL", "BUY", 400), _pos("MSFT", "BUY", 400)],
        conviction=8,
        hold_reasoning="",
    )
    lookup = _price_lookup({"AAPL": 200.0, "MSFT": 300.0})
    a = manager_decision_to_orders(decision, TRADE_DATE, lookup)
    b = manager_decision_to_orders(decision, TRADE_DATE, lookup)
    assert [o.order_id for o in a] == [o.order_id for o in b]
    assert all(o.order_id.startswith("ord_2026-06-01_the-manager_") for o in a)
    assert all(o.currency == "EUR" for o in a)


def test_decision_to_orders_empty():
    decision = ManagerDecision(positions=[], conviction=2, hold_reasoning="hold")
    orders = manager_decision_to_orders(decision, TRADE_DATE, _price_lookup({}))
    assert orders == []


# ---------------------------------------------------------------------------
# Session-step fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager_env(tmp_path, monkeypatch):
    """Redirect every path the manager step touches into tmp_path.

    Mirrors the broker_env discipline: nothing the manager step writes can land
    in the real data dirs.
    """
    import engine.orders as orders_mod
    import engine.paper_broker as broker_mod
    import scripts.daily_session as session_mod

    ohlcv = tmp_path / "ohlcv"
    ohlcv.mkdir()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    ticker_ccy = tmp_path / "ticker_currencies.json"

    public_outbox = tmp_path / "orders" / "outbox"
    public_inbox = tmp_path / "orders" / "inbox"
    manager_outbox = tmp_path / "orders" / "manager-outbox"
    manager_inbox = tmp_path / "orders" / "manager-inbox"
    manager_review = tmp_path / "orders" / "manager-review"
    for d in (public_outbox, public_inbox, manager_outbox, manager_inbox):
        d.mkdir(parents=True)

    # The session step derives portfolios from _PROJECT_ROOT / "data" / "portfolios".
    portfolios = tmp_path / "data" / "portfolios"
    portfolios.mkdir(parents=True)

    # Broker path constants.
    monkeypatch.setattr(broker_mod, "_OHLCV_STORE", ohlcv)
    monkeypatch.setattr(broker_mod, "AGENT_CONFIG_DIR", config_dir)
    monkeypatch.setattr(broker_mod, "TICKER_CURRENCIES_PATH", ticker_ccy)
    monkeypatch.setattr(broker_mod, "_TICKER_CURRENCY_OVERRIDES", None)

    # Orders module dirs (default channel + manager channel).
    monkeypatch.setattr(orders_mod, "OUTBOX_DIR", public_outbox)
    monkeypatch.setattr(orders_mod, "INBOX_DIR", public_inbox)
    monkeypatch.setattr(orders_mod, "MANAGER_OUTBOX_DIR", manager_outbox)
    monkeypatch.setattr(orders_mod, "MANAGER_INBOX_DIR", manager_inbox)
    monkeypatch.setattr(orders_mod, "MANAGER_REVIEW_DIR", manager_review)

    # Session project root → tmp (so the step derives data/ from here).
    monkeypatch.setattr(session_mod, "_PROJECT_ROOT", tmp_path)

    return {
        "tmp_path": tmp_path,
        "ohlcv": ohlcv,
        "config_dir": config_dir,
        "public_outbox": public_outbox,
        "public_inbox": public_inbox,
        "manager_outbox": manager_outbox,
        "manager_inbox": manager_inbox,
        "manager_review": manager_review,
        "portfolios": portfolios,
    }


def _seed_ohlcv(ohlcv_dir: Path, ticker: str, on: str, close: float) -> None:
    path = ohlcv_dir / f"{ticker}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"date": on, "close": close, "adj_close": close}) + "\n")


def _agent_result(tickers, bias="buy", conviction=8):
    return {
        "research_note": {
            "thesis": "Bullish on quality.",
            "conviction": conviction,
            "tickers": list(tickers),
            "action_bias": bias,
            "horizon": "medium",
            "catalysts": "earnings",
            "currency": "EUR",
        }
    }


# ---------------------------------------------------------------------------
# step_apply_manager_decision
# ---------------------------------------------------------------------------


def test_apply_manager_hold_writes_review_but_no_orders(manager_env):
    from scripts.daily_session import step_apply_manager_decision

    raw = {"positions": [], "conviction": 3, "hold_reasoning": "Nothing compelling."}

    step_apply_manager_decision(raw, TRADE_DATE)

    # No orders, no fills.
    assert read_outbox(TRADE_DATE, outbox_dir=manager_env["manager_outbox"]) == []
    assert read_inbox(TRADE_DATE, inbox_dir=manager_env["manager_inbox"]) == []

    # the-manager portfolio either absent (no trade) or unchanged at init cash.
    pm = PortfolioManager(manager_env["portfolios"])
    portfolio = pm.load("the-manager")
    assert portfolio.positions == []
    assert portfolio.cash == pytest.approx(2000.0)

    # Manager review STILL written (audit every day).
    review = manager_env["manager_review"] / f"{TRADE_DATE.isoformat()}.json"
    assert review.exists()
    payload = json.loads(review.read_text())
    assert payload["conviction"] == 3
    assert payload["positions"] == []
    assert "render" in payload


def test_apply_manager_valid_decision_fills_manager_book(manager_env):
    from scripts.daily_session import step_apply_manager_decision

    _seed_ohlcv(manager_env["ohlcv"], "AAPL", "2026-06-01", 200.0)

    raw = {
        "positions": [
            {
                "ticker": "AAPL",
                "action": "BUY",
                "size_eur": 400,
                "reasoning": "high conviction quality",
            }
        ],
        "conviction": 9,
        "hold_reasoning": "",
    }

    step_apply_manager_decision(raw, TRADE_DATE)

    # Manager outbox written.
    out = read_outbox(TRADE_DATE, outbox_dir=manager_env["manager_outbox"])
    assert len(out) == 1
    assert out[0].agent_id == "the-manager"

    # Manager inbox written with a filled fill.
    fills = read_inbox(TRADE_DATE, inbox_dir=manager_env["manager_inbox"])
    assert len(fills) == 1
    assert fills[0].status == "filled"
    assert fills[0].fees is not None and fills[0].fees > 0

    # the-manager portfolio mutated (AAPL position, cash reduced by notional+fees).
    pm = PortfolioManager(manager_env["portfolios"])
    portfolio = pm.load("the-manager")
    assert any(p.ticker == "AAPL" for p in portfolio.positions)
    assert portfolio.cash < 2000.0

    # PUBLIC inbox/outbox MUST be untouched.
    assert read_inbox(TRADE_DATE, inbox_dir=manager_env["public_inbox"]) == []
    assert read_outbox(TRADE_DATE, outbox_dir=manager_env["public_outbox"]) == []


def test_apply_manager_idempotent(manager_env):
    from scripts.daily_session import step_apply_manager_decision

    _seed_ohlcv(manager_env["ohlcv"], "AAPL", "2026-06-01", 200.0)
    raw = {
        "positions": [
            {
                "ticker": "AAPL",
                "action": "BUY",
                "size_eur": 400,
                "reasoning": "high conviction quality",
            }
        ],
        "conviction": 9,
        "hold_reasoning": "",
    }

    step_apply_manager_decision(raw, TRADE_DATE)
    pm = PortfolioManager(manager_env["portfolios"])
    cash_after_first = pm.load("the-manager").cash
    shares_after_first = pm.load("the-manager").positions[0].shares

    # Re-run: the @idempotent_step guard skips the second call entirely.
    step_apply_manager_decision(raw, TRADE_DATE)
    portfolio = pm.load("the-manager")
    assert portfolio.cash == pytest.approx(cash_after_first)
    assert portfolio.positions[0].shares == pytest.approx(shares_after_first)

    # Manager inbox has exactly one fill for the order_id.
    fills = read_inbox(TRADE_DATE, inbox_dir=manager_env["manager_inbox"])
    assert len(fills) == 1


def test_apply_manager_idempotent_at_fill_layer(manager_env):
    """Even when the step guard is genuinely defeated, fill_day's order_id
    idempotency holds.

    The @idempotent_step decorator binds `is_done` at import time, so patching
    the module name is a no-op (the body would only run once and the test would
    pass for the wrong reason). Instead we CLEAR the per-day step state between
    the two calls, forcing the body to truly execute twice — proving the
    manager-inbox order_id idempotency (not just the session-state guard)
    prevents a double-fill.
    """
    import scripts.session_state as ss
    from scripts.daily_session import step_apply_manager_decision

    _seed_ohlcv(manager_env["ohlcv"], "AAPL", "2026-06-01", 200.0)
    raw = {
        "positions": [
            {
                "ticker": "AAPL",
                "action": "BUY",
                "size_eur": 400,
                "reasoning": "high conviction quality",
            }
        ],
        "conviction": 9,
        "hold_reasoning": "",
    }

    step_apply_manager_decision(raw, TRADE_DATE)
    pm = PortfolioManager(manager_env["portfolios"])
    shares_after_first = pm.load("the-manager").positions[0].shares

    # Defeat the step-state guard for real, then re-run the body.
    ss.clear(TRADE_DATE)
    step_apply_manager_decision(raw, TRADE_DATE)

    fills = read_inbox(TRADE_DATE, inbox_dir=manager_env["manager_inbox"])
    filled = [f for f in fills if f.status == "filled"]
    assert len(filled) == 1  # fill-layer idempotency, not the step guard
    # And the book is unchanged by the second run.
    assert pm.load("the-manager").positions[0].shares == pytest.approx(
        shares_after_first
    )


def test_apply_manager_unparseable_writes_placeholder_review(manager_env):
    """Junk/None LLM output writes a placeholder review, authors no orders, and
    leaves the-manager book untouched — never crashes (session-safety contract)."""
    import json

    from scripts.daily_session import step_apply_manager_decision
    from engine.orders import read_outbox

    review_dir = manager_env["manager_review"]

    for raw in (None, {"garbage": 1}, "not even a dict"):
        # Each iteration is a fresh session-day so the step body runs.
        import scripts.session_state as ss

        ss.clear(TRADE_DATE)
        step_apply_manager_decision(raw, TRADE_DATE)  # must not raise

        # Review artifact written every day, even on unparseable input.
        review_path = review_dir / f"{TRADE_DATE.isoformat()}.json"
        assert review_path.exists()
        json.loads(review_path.read_text())  # valid JSON

        # No orders authored.
        assert read_outbox(TRADE_DATE, outbox_dir=manager_env["manager_outbox"]) == []

    # the-manager book never created/mutated (no fills ever happened).
    pm = PortfolioManager(manager_env["portfolios"])
    mgr = pm.load("the-manager")
    assert not mgr.positions


# ---------------------------------------------------------------------------
# step_build_manager_prompt
# ---------------------------------------------------------------------------


def test_build_manager_prompt_contains_notes_and_policy(manager_env):
    from scripts.daily_session import step_build_manager_prompt

    _seed_ohlcv(manager_env["ohlcv"], "AAPL", "2026-06-01", 200.0)
    agent_results = {"steady-eddie-eur": _agent_result(["AAPL"])}

    prompt = step_build_manager_prompt(agent_results, TRADE_DATE)

    assert isinstance(prompt, str) and prompt
    # The persona wrapper is applied.
    assert "PERSONA (the-manager)" in prompt
    # The C3 context block is present.
    assert "RISK BUDGET" in prompt
    assert "AAPL" in prompt


# ---------------------------------------------------------------------------
# fill_day default path unchanged by parameterization
# ---------------------------------------------------------------------------


def test_fill_day_default_path_uses_public_dirs(manager_env):
    """fill_day() with no channel args must read/write the PUBLIC dirs only."""
    from engine.orders import append_order
    from engine.paper_broker import fill_day

    _seed_ohlcv(manager_env["ohlcv"], "AAPL", "2026-06-01", 200.0)
    (manager_env["config_dir"] / "agent1.json").write_text(
        json.dumps(
            {
                "max_order_notional": 10_000.0,
                "max_orders_per_day": 10,
                "daily_drawdown_halt_pct": -50.0,
                "allowed_universe": [],
                "dry_run": False,
            }
        )
    )
    pm = PortfolioManager(manager_env["portfolios"])
    pm.initialize("agent1", initial_capital=5000.0, currency="EUR")

    order = Order(
        order_id="ord_2026-06-01_agent1_001",
        ts=__import__("datetime").datetime(
            2026, 6, 1, tzinfo=__import__("datetime").timezone.utc
        ),
        agent_id="agent1",
        action="BUY",
        ticker="AAPL",
        shares=2,
        reasoning="test",
        currency="EUR",
    )
    append_order(TRADE_DATE, order)  # default → public outbox

    fills = fill_day(TRADE_DATE, pm)  # default → public dirs

    assert len(fills) == 1 and fills[0].status == "filled"
    # Public inbox got the fill; manager inbox stayed empty.
    assert len(read_inbox(TRADE_DATE, inbox_dir=manager_env["public_inbox"])) == 1
    assert read_inbox(TRADE_DATE, inbox_dir=manager_env["manager_inbox"]) == []


# ---------------------------------------------------------------------------
# Exclusion from public surfaces
# ---------------------------------------------------------------------------


def test_the_manager_not_in_roster():
    from engine.posts import AGENT_DISPLAY_NAMES, AGENT_POST_TIMES

    assert "the-manager" not in AGENT_POST_TIMES
    assert "the-manager" not in AGENT_DISPLAY_NAMES


def test_output_bundle_excludes_the_manager():
    """The bundle's agents map is keyed off ROSTER; the-manager is not in it.

    Builds a real bundle and asserts the-manager never appears as an agent key,
    even when its result/portfolio summary is injected.
    """
    from engine.blog import BlogDraft
    from engine.output_bundle import ROSTER, assemble_output_bundle

    assert "the-manager" not in ROSTER

    blog_draft = BlogDraft(title="Day 1", body_md="Body.", slug="day-1")
    bundle = assemble_output_bundle(
        bundle_date=TRADE_DATE,
        market_data={},
        # Inject the-manager into every input — it must STILL be filtered out
        # because the bundle iterates ROSTER, not the input keys.
        agent_results={"steady-eddie-eur": {}, "the-manager": {"trades": []}},
        agent_posts={},
        portfolio_summaries={
            "steady-eddie-eur": {
                "cash": 2000.0,
                "deployed": 0.0,
                "positions": [],
                "currency": "EUR",
            },
            "the-manager": {
                "cash": 1600.0,
                "deployed": 400.0,
                "positions": [],
                "currency": "EUR",
            },
        },
        leaderboard=[],
        blog_draft=blog_draft,
        oracle_posts=[],
    )
    assert "the-manager" not in bundle["agents"]


# ---------------------------------------------------------------------------
# Task 4: Manager conditional orders route to the Manager pending channel
# ---------------------------------------------------------------------------


def test_manager_conditional_order_routes_to_manager_pending(
    manager_env, monkeypatch
) -> None:
    """A Manager conditional order (trigger+expires) must land in MANAGER_PENDING_DIR,
    NOT in the public PENDING_DIR, and write no fill to the public INBOX_DIR.

    Isolation contract:
    - pending file present in manager_pending
    - public pending dir remains empty
    - public inbox receives no fill
    """
    import datetime as dt

    from engine.orders import Order, append_order, read_inbox
    from engine.paper_broker import fill_day

    # Redirect public pending to an isolated dir so we can assert isolation.
    public_pending = manager_env["tmp_path"] / "orders" / "public-pending"
    public_pending.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("engine.triggers.PENDING_DIR", public_pending)

    # Dedicated manager pending/cancels dirs in tmp space.
    manager_pending = manager_env["tmp_path"] / "orders" / "manager-pending"
    manager_cancels = manager_env["tmp_path"] / "orders" / "manager-cancels"
    manager_pending.mkdir(parents=True, exist_ok=True)
    manager_cancels.mkdir(parents=True, exist_ok=True)

    order = Order(
        order_id="ord_2026-06-01_the-manager_conditional_001",
        ts=dt.datetime(2026, 6, 1, 20, 0, 0, tzinfo=dt.timezone.utc),
        agent_id="the-manager",
        action="BUY",
        ticker="AAPL",
        shares=1.0,
        reasoning="buy on breakout",
        currency="EUR",
        trigger={"op": ">=", "level": 210.0},
        expires="2026-07-01",
    )
    append_order(TRADE_DATE, order, outbox_dir=manager_env["manager_outbox"])

    pm = PortfolioManager(manager_env["portfolios"])
    pm.initialize("the-manager", initial_capital=2000.0, currency="EUR")

    # Call fill_day with all four Manager channel dirs — this is the NEW signature.
    fills = fill_day(
        TRADE_DATE,
        pm,
        outbox_dir=manager_env["manager_outbox"],
        inbox_dir=manager_env["manager_inbox"],
        pending_dir=manager_pending,
        cancels_dir=manager_cancels,
    )

    # Conditional order does not produce a fill record.
    assert not any(f.order_id == order.order_id for f in fills)

    # Pending file is in manager_pending (not public).
    assert (manager_pending / f"{order.order_id}.json").exists()

    # Public pending dir has no file (isolation).
    assert list(public_pending.iterdir()) == []

    # Public inbox has no fill (isolation).
    assert read_inbox(TRADE_DATE, inbox_dir=manager_env["public_inbox"]) == []


def test_public_inbox_has_no_manager_orders(manager_env):
    from scripts.daily_session import step_apply_manager_decision
    from engine.orders import inbox_order_ids

    _seed_ohlcv(manager_env["ohlcv"], "AAPL", "2026-06-01", 200.0)
    raw = {
        "positions": [
            {
                "ticker": "AAPL",
                "action": "BUY",
                "size_eur": 400,
                "reasoning": "high conviction quality",
            }
        ],
        "conviction": 9,
        "hold_reasoning": "",
    }
    step_apply_manager_decision(raw, TRADE_DATE)

    public_ids = inbox_order_ids(inbox_dir=manager_env["public_inbox"])
    assert not any("the-manager" in oid for oid in public_ids)
    assert public_ids == set()
