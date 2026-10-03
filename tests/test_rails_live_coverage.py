"""The W1 rails, checked against the live desk's actual committed state.

Live-only (see LIVE_ONLY_TESTS in scripts/sync_core.py): reads this repo's
committed portfolios, pending orders, inbox ledger, universes and OHLCV store,
none of which exist in midas-core.

Two jobs, and they pull in opposite directions on purpose:

* **Coverage** — nothing tradable may be undenominable. `ticker_currency`
  returns `None` now instead of guessing USD, which converts a silent
  mispricing into a `CURRENCY_UNRESOLVED` rejection. That is only an
  improvement if the maps actually cover what the desk trades.
* **No false positives** — the new price and trigger bands must not refuse
  anything the desk has legitimately done. A rail that would have rejected
  real history is a rail that will reject real trades on Monday.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from engine.config import get_config
from engine.paper_broker import (
    TRIGGER_LEVEL_MAX,
    TRIGGER_LEVEL_MIN,
    _price_out_of_band,
)
from engine.quotes import (
    _load_registry_currencies,
    _load_ticker_currency_overrides,
    latest_price,
    ticker_currency,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _held_tickers() -> set[str]:
    out: set[str] = set()
    for path in (REPO_ROOT / "data" / "portfolios").glob("*/portfolio.json"):
        book = json.loads(path.read_text(encoding="utf-8"))
        for position in book.get("positions", []):
            out.add(position["ticker"])
    return out


def _pending_orders() -> list[dict]:
    out: list[dict] = []
    for subdir in ("pending", "manager-pending"):
        for path in (REPO_ROOT / "data" / "orders" / subdir).glob("*.json"):
            out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def _universe_tickers() -> set[str]:
    out: set[str] = set()
    for path in (REPO_ROOT / "data" / "universes").glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = (
            raw if isinstance(raw, list) else raw.get("tickers", raw.get("symbols", []))
        )
        for item in items:
            ticker = item if isinstance(item, str) else item.get("ticker")
            if ticker:
                out.add(ticker)
    return out


# ---------------------------------------------------------------------------
# W1.4 — currency coverage
# ---------------------------------------------------------------------------


def test_every_held_and_pending_ticker_is_in_the_maps():
    """Held and pending tickers must resolve from layers 1-2, not the suffix.

    The strict bar applies here and not to the whole universe because these
    are the tickers with real state behind them: a position being valued
    every evening, or an armed order that will fill. For those, "the suffix
    says EUR" is not good enough — the vendor's own answer is available and
    is what the override map and registry exist to carry.

    Measured 2026-08-07: 40 held, 40 pending, 0 of either on the heuristic.
    """
    maps = set(_load_ticker_currency_overrides()) | set(_load_registry_currencies())
    tickers = _held_tickers() | {o["ticker"] for o in _pending_orders()}
    assert tickers, (
        "no held or pending tickers found — fixture is not exercising anything"
    )

    missing = sorted(t for t in tickers if t not in maps)
    assert missing == [], (
        "these tickers have real state but no vendor-sourced currency; add them to "
        f"data/ticker_currencies.json or re-run scripts/fetch_ohlcv.py: {missing}"
    )


def test_no_universe_ticker_is_undenominable():
    """Every tradable ticker must resolve to *something*.

    Deliberately weaker than the test above. 130 of the 1,046 universe
    tickers resolve through the suffix table rather than the maps (`.OL`,
    `.PA`, `.MC`, `.CO`, `.VI`, `.AS`, `.DE`), and that is acceptable for
    names nobody holds: a documented exchange→currency mapping is a
    different thing from the blind USD default this replaced. What is not
    acceptable is a ticker an agent could name in an order and that the
    broker would then have to refuse.
    """
    unresolved = sorted(t for t in _universe_tickers() if ticker_currency(t) is None)
    assert unresolved == [], (
        f"universe tickers with no resolvable currency: {unresolved}"
    )


# ---------------------------------------------------------------------------
# W1.2 / W1.3 — the bands must not refuse real history
# ---------------------------------------------------------------------------


def _channels() -> list[tuple[Path, Path]]:
    """(outbox, inbox) for the public channel and every allocator channel.

    Derived from the ``*inbox`` directories on disk, the way
    `engine.paper_broker._channel_pairs` derives them, so a channel added
    later is replayed without anyone remembering to list it here.
    """
    orders = REPO_ROOT / "data" / "orders"
    pairs = []
    for inbox in sorted(orders.glob("*inbox")):
        prefix = inbox.name[: -len("inbox")]
        pairs.append((orders / f"{prefix}outbox", inbox))
    return pairs


def _filled_fills() -> list[tuple[str, date, str, float]]:
    """(order_id, trade_date, ticker, fill_price) for every committed fill.

    Every channel the broker fills through: the public one and the Manager's
    (``manager-inbox``), which the rails apply to just the same. The ticker is
    not in the inbox line, so it is joined back from the channel's own outbox
    by order_id — the same join the site's trade cards make.
    """
    out: list[tuple[str, date, str, float]] = []
    for outbox, inbox in _channels():
        outbox_tickers: dict[str, str] = {}
        for path in outbox.glob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    order = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if order.get("order_id") and order.get("ticker"):
                    outbox_tickers[order["order_id"]] = order["ticker"]

        for path in sorted(inbox.glob("*.jsonl")):
            trade_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    fill = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if fill.get("status") != "filled":
                    continue
                ticker = outbox_tickers.get(fill.get("order_id", ""))
                price = fill.get("fill_price")
                if ticker and isinstance(price, (int, float)):
                    out.append((fill["order_id"], trade_date, ticker, float(price)))
    return out


def test_the_replay_covers_every_fill_channel():
    """Regression (review of feat/stage1-asof-reads, finding 4): the replays
    read only data/orders/{outbox,inbox}, so the Manager's broker-path fills
    in manager-inbox were never replayed through any rail. Every filled row in
    every ``*inbox`` must reach the replay."""
    channels = {inbox.name for _outbox, inbox in _channels()}
    assert {"inbox", "manager-inbox"} <= channels, channels

    filled: set[str] = set()
    for _outbox, inbox in _channels():
        for path in inbox.glob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    if row.get("status") == "filled":
                        filled.add(row["order_id"])
    manager = {
        json.loads(line)["order_id"]
        for path in (REPO_ROOT / "data" / "orders" / "manager-inbox").glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("status") == "filled"
    }
    assert manager, "no committed Manager fill: this control exercises nothing"
    replayed = {f[0] for f in _filled_fills()}
    assert filled - replayed == set(), f"fills no replay reads: {sorted(filled - replayed)}"


def test_the_price_band_would_not_have_refused_any_committed_fill():
    """Replay: no fill the desk has ever booked sits outside the band.

    This is the false-positive control for PRICE_IMPLAUSIBLE, and it is the
    half that matters — catching a 100x error is easy, doing it without
    refusing ordinary trades is the design constraint. Note the ledger has
    been reconciled onto ISO units (2026-08-07), so this replays the
    corrected basis, which is the basis Monday's fills will use.
    """
    fills = _filled_fills()
    assert len(fills) > 100, f"expected the full committed ledger, joined {len(fills)}"

    refused = []
    for order_id, trade_date, ticker, price in fills:
        previous = latest_price(ticker, trade_date - timedelta(days=1))
        if previous is None:
            continue
        if _price_out_of_band(price, previous.price):
            refused.append((order_id, ticker, price, previous.price))

    assert refused == [], f"the band would have refused real fills: {refused}"


#: Committed fills the STALE_PRICE rail WOULD have refused, each judged by a
#: human (plan 2026-10-03, 1.3). Order id -> why the refusal is right.
JUDGED_STALE_REFUSALS = {
    # goldfinger bought SGLN.MI on 2026-09-16 at 73.05, its 2026-09-11 close:
    # since 2026-09 the vendor served SGLN.MI one row (today's quote) for any
    # window, so the store froze while Milan traded on. Replayed at the fill's
    # own executed_sha (efedd4088) the bucket stood at 09-15, two sessions on;
    # against today's store, three. A stale fill the rail exists to refuse. The
    # ticker left the universe on 2026-09-26 for PPFB.DE.
    "ord_2026-09-16_goldfinger_001",
}


def test_the_stale_rail_would_refuse_only_the_judged_committed_fills():
    """Replay: every committed fill through STALE_PRICE, at its trade date.

    Against TODAY's store, not the one the broker saw, so it can err both
    ways. A later refill of the fill's own ticker shortens its lag, and this
    misses a refusal the rail would have made then. A later refill of the
    bucket's other members can create a majority date the broker never saw
    (the 2026-09-26 backfill added 483 US rows for 09-22, a date most of the
    bucket lacked that night), which lengthens the lag of a ticker that was
    not refilled, and this invents a refusal. The assertion is an exact set,
    so either error turns it red for a human to judge; it never licenses
    loosening the replay. The faithful replay (each fill's own executed_sha, buckets
    sampled to 40 members, 52 s) was run once on 2026-10-03: 411 fills carry a
    sha, 395 read lag 0, 15 lag 1, and the only refusal is the one above.
    """
    from engine.market_calendar import bucket_lag, MAX_BUCKET_LAG_DAYS

    fills = _filled_fills()
    assert len(fills) > 100, f"expected the full committed ledger, joined {len(fills)}"

    refused, judged = [], 0
    for order_id, trade_date, ticker, _price in fills:
        quote = latest_price(ticker, trade_date)
        if quote is None:
            continue
        lag = bucket_lag(ticker, quote.as_of, trade_date)
        if lag.lag is not None:
            judged += 1
        if lag.lag is not None and lag.lag > MAX_BUCKET_LAG_DAYS:
            refused.append(order_id)

    assert judged > 0.9 * len(fills), "the rail abstained on most of the ledger"
    assert sorted(refused) == sorted(JUDGED_STALE_REFUSALS), (
        "the stale rail would refuse committed fills nobody has judged: "
        f"{sorted(set(refused) - JUDGED_STALE_REFUSALS)}"
    )


def test_no_committed_fill_is_on_a_suspended_instrument():
    """The registry as committed would have refused no fill the desk booked."""
    from engine.instrument_status import load

    suspended = set(load())
    hit = [f for f in _filled_fills() if f[2] in suspended]
    assert hit == [], f"committed fills on instruments now suspended: {hit}"


def test_the_stale_rail_can_judge_every_held_and_pending_ticker():
    """No live position or armed order sits in a bucket too small to judge,
    where the rail would silently abstain. Measured 2026-10-03: 37 held and
    38 pending tickers, every one judged."""
    from engine.market_calendar import bucket_lag

    today = date.today()
    unjudged = []
    for ticker in sorted(_held_tickers() | {o["ticker"] for o in _pending_orders()}):
        quote = latest_price(ticker, today)
        if quote is None:
            continue
        lag = bucket_lag(ticker, quote.as_of, today)
        if lag.lag is None:
            unjudged.append((ticker, lag.bucket, lag.population))
    assert unjudged == [], f"held/pending tickers the stale rail cannot judge: {unjudged}"


def test_every_live_pending_order_is_inside_the_trigger_band():
    """Same control for TRIGGER_LEVEL_IMPLAUSIBLE, against the armed orders.

    Measured 2026-08-07: all live pending levels sit within [0.77, 2.06] of
    their ticker's close — two orders of magnitude clear of the band, and of
    the 95.7 the pence-stop incident produced.
    """
    today = date.today()
    offenders = []
    ratios = []
    for order in _pending_orders():
        trigger = order.get("trigger") or {}
        level = trigger.get("level")
        if not isinstance(level, (int, float)) or level <= 0:
            continue
        quote = latest_price(order["ticker"], today)
        if quote is None or quote.price <= 0:
            continue
        ratio = level / quote.price
        ratios.append(ratio)
        if ratio < TRIGGER_LEVEL_MIN or ratio > TRIGGER_LEVEL_MAX:
            offenders.append(
                (order.get("order_id"), order["ticker"], level, quote.price)
            )

    assert ratios, "no priced pending orders found — this test asserted nothing"
    assert offenders == [], f"live pending orders outside the trigger band: {offenders}"


@pytest.mark.parametrize(
    "level, price, expected",
    [
        (111.0, 1.16, True),  # incident #9: a stop authored in pence
        (1.04, 1.16, False),  # an ordinary 10% stop
        (2.06, 1.16, False),  # the widest ratio live today
    ],
)
def test_the_trigger_band_separates_the_incident_from_real_orders(
    level, price, expected
):
    """The band has to sit between the two populations, not merely above one."""
    ratio = level / price
    assert (ratio < TRIGGER_LEVEL_MIN or ratio > TRIGGER_LEVEL_MAX) is expected


def test_every_live_agent_ranks_on_a_measured_vs_benchmark():
    """The 2026-08-14 metric change, checked against the committed desk.

    Ranking moved from raw EUR return to `vs_benchmark_pp`. The null-last
    fallback exists for forks without baselines — on the live desk it must
    never engage: every trading agent has a benchmark series, so every row
    must carry a measured value. A None here means a baseline file went
    missing or unreadable, and the board silently degraded to the old raw
    ranking without anyone deciding that.

    Also pins the decomposition this change was built on: the vs-benchmark
    figure must be FX-free, i.e. differ from (EUR return - benchmark return)
    by the translation leg on USD books.
    """
    from engine.leaderboard import build_leaderboard_rows
    from scripts.daily_session import build_portfolio_summaries

    summaries = build_portfolio_summaries()
    assert len(summaries) == 10, "expected the full 10-agent roster on disk"
    rows = build_leaderboard_rows(summaries, on=None)

    unmeasured = [r["agent"] for r in rows if r["vs_benchmark_pp"] is None]
    assert unmeasured == [], f"agents ranked without a benchmark: {unmeasured}"

    by_agent = {r["agent"]: r for r in rows}
    # Every USD book carries its translation leg; no EUR book does.
    for agent_id, summary in summaries.items():
        if summary["currency"] == "EUR":
            assert "fx_translation_pp" not in by_agent[agent_id]
        else:
            assert "fx_translation_pp" in by_agent[agent_id]
