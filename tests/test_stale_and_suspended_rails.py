"""INSTRUMENT_SUSPENDED and STALE_PRICE (market data plan 2026-10-03, 1.3).

Until these rails existed every read path was blind to a price's age. The
broker filled CTVA on 2026-10-02 at its 2026-09-30 close of 77.65 (the real
price was 11.92), and PRICE_IMPLAUSIBLE could not object: its BUY reference
is the prior close from the same frozen file, so the ratio was exactly 1.0.

The fixtures are the real incidents, rebuilt as small stores: CTVA at
``0f981dd99`` (store at 09-30, one quarantined row for 10-01), MNST in August
(frozen at its 08-10 close through an unrestated 2:1 split), 4GLD.DE (a UCITS
fund that lands about a day after its exchange, and must keep trading) and
Xetra's 2026-05-01 holiday (a whole bucket closed, which must stay green).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from engine.config import get_config
from engine.orders import append_order, read_inbox
from tests.test_paper_broker import (  # noqa: F401 — broker_env is a fixture
    _init_portfolio,
    _make_order,
    _seed_ohlcv,
    _write_config,
    broker_env,
)

# Five peers is the smallest bucket the rail judges (MIN_BUCKET_POPULATION).
US_PEERS = ("AAPL", "MSFT", "KO", "PEP", "JNJ", "XOM")
DE_PEERS = ("SAP.DE", "SIE.DE", "ALV.DE", "BMW.DE", "BAS.DE", "DTE.DE")

# CTVA's quarantined row, verbatim from data/market/quarantine/CTVA.jsonl at
# 0f981dd99 (embedded: CI checks out at fetch-depth 1).
CTVA_QUARANTINE = {
    "symbol": "CTVA",
    "date": "2026-10-01",
    "kind": "new-row",
    "stored_close": 77.6500015258789,
    "incoming_close": 12.569999694824219,
    "ratio": 0.16188022469819185,
}
CTVA_STORE = [
    ("2026-09-28", 77.73999786376953),
    ("2026-09-29", 77.87000274658203),
    ("2026-09-30", 77.6500015258789),
]


def _weekdays(start: date, end: date, skip: frozenset[date] = frozenset()) -> list[str]:
    out = []
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in skip:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _seed_bucket(ohlcv: Path, tickers, dates: list[str], close: float = 100.0) -> None:
    for t in tickers:
        _seed_ohlcv(ohlcv, t, [(d, close) for d in dates])


def _seed_registry_from_quarantine(rows_by_symbol: dict[str, list[dict]]) -> None:
    """Build the registry the way 1.2 seeds it: from unadjudicated quarantine rows."""
    from engine import instrument_status

    market = get_config().ohlcv_dir.parent
    qdir = market / "quarantine"
    qdir.mkdir(parents=True, exist_ok=True)
    for symbol, rows in rows_by_symbol.items():
        (qdir / f"{symbol}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )
    entries = instrument_status.seed_entries(
        qdir, market / "corporate_actions.jsonl", get_config().ohlcv_dir
    )
    instrument_status._save(entries, instrument_status.registry_path())


def _hold(pm, agent_id: str, ticker: str, shares: float, price: float) -> None:
    from datetime import datetime, timezone

    from engine.types import Trade

    pm.apply_trade(
        agent_id,
        Trade(
            id=f"seed_{agent_id}_{ticker}",
            timestamp=datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc),
            action="BUY",
            ticker=ticker,
            shares=shares,
            price=price,
            total=shares * price,
            fees=0.0,
            reasoning="seed a holding",
        ),
    )


# ---------------------------------------------------------------------------
# CTVA, 2026-10-02
# ---------------------------------------------------------------------------


@pytest.fixture
def ctva_store(broker_env):
    """The store as it stood at 0f981dd99: US peers through 10-02, CTVA at 09-30."""
    _seed_bucket(
        broker_env["ohlcv"], US_PEERS, _weekdays(date(2026, 9, 14), date(2026, 10, 2))
    )
    _seed_ohlcv(broker_env["ohlcv"], "CTVA", CTVA_STORE)
    _write_config(broker_env["config_dir"], "agent1")
    return broker_env


def test_ctva_buy_on_2026_10_02_is_refused_suspended(ctva_store):
    """Regression: the CTVA fill of 2026-10-02 (plan 2026-10-03, 1.3). The
    registry is seeded from CTVA's real quarantine row, so this also holds the
    chain tripwire -> registry -> broker together."""
    from engine.paper_broker import fill_day

    _seed_registry_from_quarantine({"CTVA": [CTVA_QUARANTINE]})
    pm = _init_portfolio(ctva_store["pm_base"], "agent1", cash=10_000.0)
    append_order(date(2026, 10, 2), _make_order("o_ctva", "agent1", "BUY", "CTVA", 10))

    fills = fill_day(date(2026, 10, 2), pm)
    assert [(f.status, f.reason) for f in fills] == [("rejected", "INSTRUMENT_SUSPENDED")]
    assert pm.load("agent1").positions == []


def test_ctva_without_a_registry_entry_is_refused_stale(ctva_store):
    """The two rails are independent: with no status recorded, the price's own
    date (09-30, two US sessions behind 10-02) still refuses it."""
    from engine.paper_broker import fill_day

    pm = _init_portfolio(ctva_store["pm_base"], "agent1", cash=10_000.0)
    append_order(date(2026, 10, 2), _make_order("o_ctva", "agent1", "BUY", "CTVA", 10))

    assert [f.reason for f in fill_day(date(2026, 10, 2), pm)] == ["STALE_PRICE"]


def test_the_band_alone_would_have_filled_ctva(ctva_store):
    """Why the new rails are needed: the band compares the frozen close with
    the frozen close before it, and passes."""
    from engine.paper_broker import _price_out_of_band
    from engine.quotes import latest_price

    on = date(2026, 10, 2)
    quote = latest_price("CTVA", on)
    previous = latest_price("CTVA", on - timedelta(days=1))
    assert quote.as_of == date(2026, 9, 30)
    assert not _price_out_of_band(quote.price, previous.price)


def test_a_peer_in_the_same_store_still_fills(ctva_store):
    # Control: the fixture is not refusing everything.
    from engine.paper_broker import fill_day

    pm = _init_portfolio(ctva_store["pm_base"], "agent1", cash=10_000.0)
    append_order(date(2026, 10, 2), _make_order("o_aapl", "agent1", "BUY", "AAPL", 10))

    assert [f.status for f in fill_day(date(2026, 10, 2), pm)] == ["filled"]


def test_a_sell_of_a_suspended_holding_is_refused_and_names_the_holders(ctva_store):
    """A refused SELL traps the holder (intended: no price, no fill), so the
    session's concern trailer names every book holding the ticker."""
    from engine.paper_broker import fill_day, instrument_refusal_concerns

    pm = _init_portfolio(ctva_store["pm_base"], "agent1", cash=10_000.0)
    _init_portfolio(ctva_store["pm_base"], "agent2", cash=10_000.0)
    _init_portfolio(ctva_store["pm_base"], "agent3", cash=10_000.0)
    _hold(pm, "agent1", "CTVA", 5, 70.0)
    _hold(pm, "agent2", "CTVA", 3, 70.0)
    _seed_registry_from_quarantine({"CTVA": [CTVA_QUARANTINE]})
    on = date(2026, 10, 2)
    append_order(on, _make_order("o_out", "agent1", "SELL", "CTVA", 5))

    fills = fill_day(on, pm)
    assert [f.reason for f in fills] == ["INSTRUMENT_SUSPENDED"]
    assert pm.load("agent1").positions[0].shares == 5

    concerns = instrument_refusal_concerns(on, portfolios_dir=ctva_store["pm_base"])
    assert len(concerns) == 1
    assert "o_out" in concerns[0] and "SELL CTVA" in concerns[0]
    assert "agent1, agent2" in concerns[0] and "agent3" not in concerns[0]


def test_no_refusal_means_no_concern(ctva_store):
    from engine.paper_broker import fill_day, instrument_refusal_concerns

    pm = _init_portfolio(ctva_store["pm_base"], "agent1", cash=10_000.0)
    append_order(date(2026, 10, 2), _make_order("o_aapl", "agent1", "BUY", "AAPL", 1))
    fill_day(date(2026, 10, 2), pm)
    assert instrument_refusal_concerns(date(2026, 10, 2)) == []


def test_a_conditional_on_a_suspended_ticker_never_arms(ctva_store):
    from engine.paper_broker import fill_day
    from engine.triggers import list_pending

    _seed_registry_from_quarantine({"CTVA": [CTVA_QUARANTINE]})
    pm = _init_portfolio(ctva_store["pm_base"], "agent1", cash=10_000.0)
    order = _make_order("o_trig", "agent1", "BUY", "CTVA", 5)
    order.trigger = {"op": "<=", "level": 70.0}
    order.expires = "2026-10-30"
    append_order(date(2026, 10, 2), order)

    fill_day(date(2026, 10, 2), pm)
    assert [f.reason for f in read_inbox(date(2026, 10, 2))] == ["INSTRUMENT_SUSPENDED"]
    assert list_pending() == []


def test_a_conditional_on_a_merely_stale_ticker_still_arms(ctva_store):
    """Staleness is transient and is checked when the order fires, not at intake."""
    from engine.paper_broker import fill_day
    from engine.triggers import list_pending

    pm = _init_portfolio(ctva_store["pm_base"], "agent1", cash=10_000.0)
    order = _make_order("o_trig", "agent1", "BUY", "CTVA", 5)
    order.trigger = {"op": "<=", "level": 70.0}
    order.expires = "2026-10-30"
    append_order(date(2026, 10, 2), order)

    fill_day(date(2026, 10, 2), pm)
    assert [o.order_id for o in list_pending()] == ["o_trig"]


@pytest.mark.parametrize("registry", [True, False])
def test_a_fire_on_ctva_is_refused(ctva_store, registry):
    from engine.paper_broker import execute_triggered_order

    if registry:
        _seed_registry_from_quarantine({"CTVA": [CTVA_QUARANTINE]})
    pm = _init_portfolio(ctva_store["pm_base"], "agent1", cash=10_000.0)
    order = _make_order("o_fire", "agent1", "BUY", "CTVA", 5)
    order.trigger = {"op": "<=", "level": 78.0}
    order.expires = "2026-10-30"

    fill = execute_triggered_order(
        order, date(2026, 10, 2), pm, fire_price=77.65, fire_as_of=date(2026, 9, 30)
    )
    assert fill is not None and fill.trigger_fired
    expected = "INSTRUMENT_SUSPENDED" if registry else "STALE_PRICE"
    assert (fill.status, fill.reason) == ("rejected", expected)


def test_a_fire_on_a_current_price_fills(ctva_store):
    # Control for the fire path: same order shape on a peer at today's close.
    from engine.paper_broker import execute_triggered_order

    pm = _init_portfolio(ctva_store["pm_base"], "agent1", cash=10_000.0)
    order = _make_order("o_fire", "agent1", "BUY", "AAPL", 5)
    order.trigger = {"op": "<=", "level": 101.0}
    order.expires = "2026-10-30"
    fill = execute_triggered_order(
        order, date(2026, 10, 2), pm, fire_price=100.0, fire_as_of=date(2026, 10, 2)
    )
    assert fill is not None and fill.status == "filled"


def test_an_unreadable_registry_refuses_every_ticker(ctva_store):
    """Fail closed: a registry that cannot say which instruments are broken
    cannot vouch for any of them."""
    from engine import instrument_status
    from engine.paper_broker import fill_day

    instrument_status.registry_path().write_text("{not json", encoding="utf-8")
    pm = _init_portfolio(ctva_store["pm_base"], "agent1", cash=10_000.0)
    append_order(date(2026, 10, 2), _make_order("o_aapl", "agent1", "BUY", "AAPL", 1))

    assert [f.reason for f in fill_day(date(2026, 10, 2), pm)] == ["INSTRUMENT_SUSPENDED"]


# ---------------------------------------------------------------------------
# MNST, August 2026
# ---------------------------------------------------------------------------

MNST_QUARANTINE = [
    {"symbol": "MNST", "date": "2026-08-11", "kind": "new-row",
     "stored_close": 91.43000030517578, "incoming_close": 45.529998779296875,
     "ratio": 0.49797657910233495},
    {"symbol": "MNST", "date": "2026-08-12", "kind": "new-row",
     "stored_close": 91.43000030517578, "incoming_close": 45.97999954223633,
     "ratio": 0.5028983855273316},
]


@pytest.fixture
def mnst_store(broker_env):
    """Store as of the 2026-08-13 session (20:00 UTC then, so the store held
    the previous day): US peers through 08-12, MNST frozen at its 08-10 close
    of 91.43 while the vendor served 45.53 for 08-11 on the split basis."""
    _seed_bucket(
        broker_env["ohlcv"], US_PEERS, _weekdays(date(2026, 7, 20), date(2026, 8, 12))
    )
    _seed_ohlcv(
        broker_env["ohlcv"],
        "MNST",
        [(d, 91.43) for d in _weekdays(date(2026, 7, 20), date(2026, 8, 10))],
    )
    _write_config(broker_env["config_dir"], "agent1")
    return broker_env


@pytest.mark.parametrize(
    "registry, expected",
    [(True, "INSTRUMENT_SUSPENDED"), (False, "STALE_PRICE")],
)
def test_mnst_august_buy_is_refused(mnst_store, registry, expected):
    """Regression: MNST sat buyable at twice its price from 2026-08-11 to the
    08-17 adjudication (skill midas-market-data, "A quarantined row is
    adjudicated")."""
    from engine.paper_broker import fill_day

    if registry:
        _seed_registry_from_quarantine({"MNST": MNST_QUARANTINE})
    pm = _init_portfolio(mnst_store["pm_base"], "agent1", cash=10_000.0)
    append_order(date(2026, 8, 13), _make_order("o_mnst", "agent1", "BUY", "MNST", 10))

    assert [f.reason for f in fill_day(date(2026, 8, 13), pm)] == [expected]


def test_mnst_one_session_behind_is_the_suspension_rails_to_catch(mnst_store):
    """On 08-12 MNST trailed the US bucket by one session only, inside the
    stale tolerance: the stale rail alone fills it at 91.43, which is why the
    suspension rail is not optional. Pinned so nobody reads the stale rail as
    covering a split on its first night."""
    from engine.paper_broker import fill_day

    pm = _init_portfolio(mnst_store["pm_base"], "agent1", cash=10_000.0)
    append_order(date(2026, 8, 12), _make_order("o1", "agent1", "BUY", "MNST", 1))
    # The store as of the 08-12 session held the bucket through 08-11.
    for t in US_PEERS:
        path = mnst_store["ohlcv"] / f"{t}.jsonl"
        rows = [r for r in path.read_text().splitlines() if '"2026-08-12"' not in r]
        path.write_text("\n".join(rows) + "\n")
    assert [f.status for f in fill_day(date(2026, 8, 12), pm)] == ["filled"]

    _seed_registry_from_quarantine({"MNST": MNST_QUARANTINE[:1]})
    append_order(date(2026, 8, 12), _make_order("o2", "agent1", "BUY", "MNST", 1))
    assert [f.reason for f in fill_day(date(2026, 8, 12), pm)] == ["INSTRUMENT_SUSPENDED"]


# ---------------------------------------------------------------------------
# 4GLD.DE one day late; Xetra closed on 2026-05-01
# ---------------------------------------------------------------------------


def test_a_fund_one_day_behind_its_exchange_still_fills(broker_env):
    """4GLD.DE lands about a day after .DE on most nights (measured
    2026-10-03); a one-session tolerance keeps it tradable."""
    from engine.paper_broker import fill_day

    on = date(2026, 10, 2)
    _seed_bucket(broker_env["ohlcv"], DE_PEERS, _weekdays(date(2026, 9, 14), on))
    _seed_ohlcv(
        broker_env["ohlcv"],
        "4GLD.DE",
        [(d, 120.0) for d in _weekdays(date(2026, 9, 14), on - timedelta(days=1))],
    )
    _write_config(broker_env["config_dir"], "agent1")
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=10_000.0, currency="EUR")
    append_order(on, _make_order("o_gld", "agent1", "BUY", "4GLD.DE", 1, "EUR"))

    fills = fill_day(on, pm)
    assert [(f.status, f.fill_price) for f in fills] == [("filled", 120.0)]


def test_two_days_behind_its_exchange_is_refused(broker_env):
    # The same fund one more session late: the tolerance is one day, not two.
    from engine.paper_broker import fill_day

    on = date(2026, 10, 2)
    _seed_bucket(broker_env["ohlcv"], DE_PEERS, _weekdays(date(2026, 9, 14), on))
    _seed_ohlcv(
        broker_env["ohlcv"],
        "4GLD.DE",
        [(d, 120.0) for d in _weekdays(date(2026, 9, 14), on - timedelta(days=2))],
    )
    _write_config(broker_env["config_dir"], "agent1")
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=10_000.0, currency="EUR")
    append_order(on, _make_order("o_gld", "agent1", "BUY", "4GLD.DE", 1, "EUR"))

    assert [f.reason for f in fill_day(on, pm)] == ["STALE_PRICE"]


def test_a_whole_exchange_holiday_fills(broker_env):
    """Xetra was closed on 2026-05-01 (Labour Day): a session that day sees
    every .DE name at 04-30. Nobody is behind anybody: the bucket did not
    trade, so its reference date does not move."""
    from engine.paper_broker import fill_day

    holiday = frozenset({date(2026, 5, 1)})
    dates = _weekdays(date(2026, 4, 6), date(2026, 5, 1), skip=holiday)
    assert dates[-1] == "2026-04-30"
    _seed_bucket(broker_env["ohlcv"], DE_PEERS + ("4GLD.DE",), dates)
    _write_config(broker_env["config_dir"], "agent1")
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=10_000.0, currency="EUR")
    append_order(date(2026, 5, 1), _make_order("o_hol", "agent1", "BUY", "SAP.DE", 1, "EUR"))

    assert [f.status for f in fill_day(date(2026, 5, 1), pm)] == ["filled"]


def test_a_bucket_too_small_to_judge_abstains(broker_env):
    """Below MIN_BUCKET_POPULATION the rail says nothing rather than reading a
    one-file bucket as agreeing with itself; existing single-ticker fixtures
    across the suite rely on that."""
    from engine.paper_broker import fill_day

    _seed_ohlcv(broker_env["ohlcv"], "VOO", [("2026-09-01", 500.0)])
    _write_config(broker_env["config_dir"], "agent1")
    pm = _init_portfolio(broker_env["pm_base"], "agent1", cash=10_000.0)
    append_order(date(2026, 10, 2), _make_order("o", "agent1", "BUY", "VOO", 1))
    assert [f.status for f in fill_day(date(2026, 10, 2), pm)] == ["filled"]
