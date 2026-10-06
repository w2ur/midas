"""Per-agent benchmark + coin-flip phantom competitors.

Data model: each baseline is a list of daily snapshots
{date, portfolio_value, cash, positions_value, currency} mirroring
the shape of data/portfolios/<agent>/snapshots.json so the site can
consume baselines with minimal new code. A passive benchmark row priced
from the store also records the closes it used ({mark_date, mark_close,
base_date, base_close}, see ``compute_passive_benchmark``); rows published
before those fields existed keep theirs in a ``<series>_marks.json`` sidecar
beside the series, never on the row (``merge_baseline_series``).

Ticker choices:
- VGK  (Vanguard FTSE Europe ETF, USD-listed) replaces IMEU.L / IWDA.L UCITS
  variants which are not reliably available via yfinance. VGK tracks FTSE
  Developed Europe, consistent with engine/market_data.py conventions.
- URTH (iShares MSCI World ETF, USD-listed) replaces IWDA.L for world / global
  reference. URTH is the same proxy already used for msci_world in
  engine/market_data.py BENCHMARK_TICKERS.

Currency is the DISPLAY currency for the series (matches the agent's home
currency). The price ratio used to compute daily value is currency-invariant,
so the ETF's actual trading currency (USD for VGK/URTH) is not relevant to
the comparison. FX-noise over the short observation window is accepted as
de minimis, matching the existing snapshot-benchmark pattern in the site.

That argument holds for one ticker's ratio and **not for the coin flip**,
which sums several tickers' closes into one book: each close is converted
into the series currency before it is summed, at the rate of the row's own
date, the rule the books are valued by (``engine.valuation.book_rate``;
``_Closes``, ``_step``; METHODOLOGY ``#coinflip-currency-2026-10-05``).
"""

from __future__ import annotations

import bisect
import json
from collections import Counter
import math
import random
from dataclasses import dataclass, fields
from datetime import date, timedelta
from pathlib import Path
from typing import Collection, Iterator, Mapping, Sequence

from engine.config import BenchmarkSpec, get_config
from engine.disclosure import require_changelog_entry
from engine.selectors.seeding import make_seed
from engine.valuation import (
    CURRENCY_UNRESOLVED,
    NO_FX_RATE,
    NO_PRICE_DATA,
    book_rate,
)


def _initial() -> float:
    """Return the initial capital from config."""
    return get_config().initial_capital


def _daterange(start: date, end: date) -> Iterator[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _load_ohlcv(ticker: str) -> dict[str, float]:
    """Return date_iso -> raw close for the ticker, empty if file missing.

    Raw `close`, never `adj_close` — see the `engine.ohlcv_store` module
    docstring. It matters most here: the passive benchmark and the coin flip
    are the controls the agents are graded against, and the agent curve is a
    price-return series (the broker credits no dividend cash). A control on a
    total-return basis would beat every agent by the market's dividend yield
    and none of that gap would be skill.
    """
    path = get_config().ohlcv_dir / f"{ticker}.jsonl"
    if not path.exists():
        return {}
    out: dict[str, float] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        out[row["date"]] = float(row["close"])
    return out


def compute_passive_benchmark(
    spec: BenchmarkSpec,
    from_date: date,
    to_date: date,
    *,
    closes: Mapping[str, float] | None = None,
) -> list[dict]:
    """€10k (or $10k) buy-and-hold of spec.ticker from from_date to to_date inclusive.

    Non-trading days carry the last observed close. Missing OHLCV data returns
    an empty list (caller treats as "no line to draw").

    **Each priced row records the two closes it was priced from**:
    ``mark_date``/``mark_close`` (the newest close on or before the row's date,
    i.e. the forward-filled one on a day the store has no bar for) and
    ``base_date``/``base_close`` (the first close in the window). The value is
    ``initial * mark_close / base_close`` exactly, so the row is
    self-describing: ``merge_baseline_series`` can tell a point that
    forward-filled a close which landed later (expected, permanent, not a
    concern) from a point whose recorded close the store has since revised
    (a genuinely wrong published price). Without them those two look
    identical — the 2026-10-01 session refused 1,830 points as one count, 11
    of them real. ``EUR_CASH_FLAT`` reads no price and records no marks.

    ``closes`` is the ticker's ``{date: close}`` when the caller has already
    read it (``build_all_baselines`` hands the same map to the merge);
    omitted, it is read from the store here.
    """
    initial = _initial()
    if spec.ticker == "EUR_CASH_FLAT":
        return [
            {
                "date": d.isoformat(),
                "portfolio_value": initial,
                "cash": initial,
                "positions_value": 0.0,
                "currency": spec.currency,
            }
            for d in _daterange(from_date, to_date)
        ]

    if closes is None:
        closes = _load_ohlcv(spec.ticker)
    if not closes:
        return []

    first_close: float | None = None
    first_date: str | None = None
    last_close: float | None = None
    last_date: str | None = None
    out: list[dict] = []
    for d in _daterange(from_date, to_date):
        iso = d.isoformat()
        if iso in closes:
            last_close = closes[iso]
            last_date = iso
            if first_close is None:
                first_close = last_close
                first_date = iso
        if first_close is None or last_close is None:
            continue  # no data yet for the range
        value = initial * (last_close / first_close)
        out.append(
            {
                "date": iso,
                "portfolio_value": value,
                "cash": 0.0,
                "positions_value": value,
                "currency": spec.currency,
                "mark_date": last_date,
                "mark_close": last_close,
                "base_date": first_date,
                "base_close": first_close,
            }
        )
    return out


# ---------------------------------------------------------------------------
# The coin flip: a stateful, path-continuous control (plan 1.6, 2026-10-05)
# ---------------------------------------------------------------------------

#: Version of the persisted coin-flip state document. 2 (2026-10-05) added
#: each holding's ``currency`` and ``mark_rate``: version 1 recorded native
#: closes and summed them into the book unconverted. 3 (2026-10-06) reads
#: ``mark_rate`` at the state's date, the valuation date, where 2 read it at
#: the date of the close: the same field with another meaning, so a version
#: 2 document is refused rather than read (``load_coin_flip_state``).
COINFLIP_STATE_SCHEMA = 3

#: What a version 2 state is told (``load_coin_flip_state``).
_SCHEMA_2_REFUSED = (
    "schema 2 recorded each holding's rate at its close's own date; this "
    "engine values at the rate of the valuation date (schema 3), so the state "
    "must be written again"
)

# The reasons a held name cannot be valued (``NO_PRICE_DATA``,
# ``CURRENCY_UNRESOLVED``, ``NO_FX_RATE``) are ``engine.valuation``'s,
# imported above and re-exported here for the callers that read them from
# this module.


@dataclass(frozen=True)
class CoinFlipHolding:
    """One position of the coin flip, recorded scale-invariantly.

    ``mark_close`` is what one share was worth when the holding was last
    marked, **in its own quote currency** ``currency`` (the ISO code
    ``engine.quotes.ticker_currency`` resolves), and ``mark_date`` the store
    date of the close that mark was read from. ``mark_rate`` is the book
    currency per unit of ``currency`` on the date the holding was last valued
    (the state's date, ``engine.valuation.book_rate``), so ``shares *
    mark_close * mark_rate`` is the holding's value in the book at its mark —
    what a holding that cannot be valued is held at.

    The next valuation, on ``d``, is ``shares * (mark_close * close(d) /
    close(mark_date)) * rate(d)``: the bracket is the native price at ``d``,
    both closes read from the *current* store, so a constant rescale of the
    symbol's history (a unit change, a restated split) cancels; the rate is
    read on ``d`` itself, the valuation date, however old the close is — the
    rule ``engine.valuation.value_position`` values the books by.
    ``mark_date`` is the close's own date rather than the state date so that
    a close landing late for the state date is credited to the next row
    instead of being lost from the path.
    """

    shares: int
    mark_date: str
    mark_close: float
    currency: str
    mark_rate: float

    @property
    def mark_value(self) -> float:
        """The holding's value in the book currency at its mark."""
        return self.shares * self.mark_close * self.mark_rate


@dataclass(frozen=True)
class CoinFlipState:
    """Where an agent's coin flip stands after the row dated ``date``."""

    date: str
    portfolio_value: float
    cash: float
    holdings: Mapping[str, CoinFlipHolding]


@dataclass(frozen=True)
class CoinFlipAdvance:
    """What one ``advance_coin_flip`` call did: rows appended, and every
    concern it printed as a ``[WARN]`` line."""

    appended: int
    concerns: list[str]


#: What a coin flip that cannot be advanced is told to do (review I2). The
#: remedy is not free: a re-init repicks the book at the last published row
#: with today's universe and store, which is a new seam in the path.
COINFLIP_REINIT_REMEDY = (
    "Recovery: scripts/init_coinflip_state.py --force, committed as its own "
    "chore(data): commit (--force rewrites every agent's state, so keep only "
    "this agent's file in it). A re-init is a new seam in the path and must be "
    "disclosed in METHODOLOGY."
)


def coin_flip_state_path(series_path: Path) -> Path:
    """``<agent>/coinflip.json`` -> ``<agent>/state/coinflip.json``.

    Outside the published series on purpose: the state is rewritten on every
    advance, the series is append-only.
    """
    return series_path.parent / "state" / series_path.name


def coin_flip_state_doc(state: CoinFlipState, agent_id: str) -> dict:
    """The JSON document persisted for ``state`` (an object, never a list)."""
    return {
        "schema": COINFLIP_STATE_SCHEMA,
        "agent": agent_id,
        "date": state.date,
        "portfolio_value": state.portfolio_value,
        "cash": state.cash,
        "holdings": {
            t: {
                "shares": h.shares,
                "currency": h.currency,
                "mark_date": h.mark_date,
                "mark_close": h.mark_close,
                "mark_rate": h.mark_rate,
            }
            for t, h in sorted(state.holdings.items())
        },
    }


def write_coin_flip_state(path: Path, state: CoinFlipState, agent_id: str) -> None:
    """Persist ``state``. Floats round-trip exactly through ``json``, which is
    what makes N one-day advances equal one N-day advance."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(coin_flip_state_doc(state, agent_id), indent=2) + "\n")


def load_coin_flip_state(path: Path) -> CoinFlipState:
    """Read a persisted state. Raises ``ValueError`` on any malformed document."""
    try:
        doc = json.loads(path.read_text())
        if doc.get("schema") == 2:
            raise ValueError(_SCHEMA_2_REFUSED)
        if doc.get("schema") != COINFLIP_STATE_SCHEMA:
            raise ValueError(f"schema is not {COINFLIP_STATE_SCHEMA}")
        date.fromisoformat(doc["date"])
        holdings = {}
        for ticker, h in doc["holdings"].items():
            date.fromisoformat(h["mark_date"])
            if not isinstance(h["currency"], str) or not h["currency"]:
                raise ValueError(f"{ticker}: holding has no currency")
            holdings[ticker] = CoinFlipHolding(
                int(h["shares"]),
                h["mark_date"],
                float(h["mark_close"]),
                h["currency"],
                float(h["mark_rate"]),
            )
        return CoinFlipState(
            date=doc["date"],
            portfolio_value=float(doc["portfolio_value"]),
            cash=float(doc["cash"]),
            holdings=holdings,
        )
    except ValueError:
        raise
    except (KeyError, TypeError, AttributeError, OSError) as exc:
        raise ValueError(f"malformed coin-flip state ({exc.__class__.__name__}: {exc})") from exc


class _Closes:
    """The current store's closes, with the last close on or before a date,
    and each ticker's currency and its rate into the book currency.

    Every amount the coin flip sums is in ``book_currency``: a close is in
    its ticker's quote currency, and is converted before it is added to
    anything (CLAUDE.md, "cross-currency positions must be converted before
    summing, on EVERY pricing path"), at the rate of the **valuation date**,
    by ``engine.valuation.book_rate`` — the books' own rule and reasons.

    Only the price read is the coin flip's own: a holding is valued by the
    ratio of two closes of the same store (``CoinFlipHolding``), which
    ``value_position``'s absolute close cannot express, and every candidate
    is read on every day, so the closes are read once and searched here.
    Currency comes from ``engine.quotes.ticker_currency``, as in
    ``value_position``.
    """

    def __init__(self, tickers: Collection[str], book_currency: str) -> None:
        self.book_currency = book_currency
        self._dates: dict[str, list[str]] = {}
        self._values: dict[str, list[float]] = {}
        self._currencies: dict[str, str | None] = {}
        self._rates: dict[tuple[str, str], tuple[float | None, str | None]] = {}
        for t in tickers:
            closes = _load_ohlcv(t)
            if closes:
                ordered = sorted(closes)
                self._dates[t] = ordered
                self._values[t] = [closes[d] for d in ordered]

    def currency(self, ticker: str) -> str | None:
        """The ticker's ISO quote currency, or None when no layer resolves it."""
        if ticker not in self._currencies:
            from engine.quotes import ticker_currency

            self._currencies[ticker] = ticker_currency(ticker)
        return self._currencies[ticker]

    def rate(self, currency: str | None, iso: str) -> tuple[float | None, str | None]:
        """``engine.valuation.book_rate`` into the book on the valuation date
        ``iso``, cached: ``(rate, None)`` or ``(None, reason)``."""
        key = (currency, iso)
        if key not in self._rates:
            self._rates[key] = book_rate(
                currency, self.book_currency, date.fromisoformat(iso)
            )
        return self._rates[key]

    def priced(self, ticker: str, iso: str) -> tuple[str, float, str, float] | None:
        """``(close date, native close, currency, rate)`` valued on ``iso``,
        or None when the ticker has no close, no resolvable currency or no
        rate on ``iso``."""
        mark = self.at(ticker, iso)
        if mark is None:
            return None
        ccy = self.currency(ticker)
        rate, _ = self.rate(ccy, iso)
        if ccy is None or rate is None:
            return None
        return mark[0], mark[1], ccy, rate

    def has_any(self) -> bool:
        return bool(self._dates)

    def at(self, ticker: str, iso: str) -> tuple[str, float] | None:
        """``(date, close)`` of the newest close on or before ``iso``, or None."""
        dates = self._dates.get(ticker)
        if not dates:
            return None
        i = bisect.bisect_right(dates, iso)
        if i == 0:
            return None
        return dates[i - 1], self._values[ticker][i - 1]


def _step(
    agent_id: str,
    holdings: Mapping[str, CoinFlipHolding],
    cash: float,
    iso: str,
    *,
    closes: _Closes,
    universe: list[str],
    excluded: Collection[str],
    max_positions: int,
    frozen: dict[str, tuple[str, CoinFlipHolding]],
) -> CoinFlipState:
    """One day of the coin flip: value the book at ``iso``, then repick.

    The semantics of the bt pipeline this replaced (``RunDaily ->
    SelectRandomlySeeded -> WeighEqually -> LimitWeights(1/n) -> Rebalance``):
    a full repick every calendar day, candidates the tickers with a close on
    or before the day, equal weight capped at ``1/max_positions`` (so fewer
    candidates than slots leaves the residue in cash), whole shares rounded
    down, zero fees, the rest in cash. Two differences, both deliberate: the
    draw is seeded by ``(agent, date)`` over the *sorted* candidates, so a
    day's pick does not depend on any earlier draw or on the order a universe
    lists its tickers; and a holding the store can no longer price, or one the
    instrument registry marks ``suspended``/``delisted``, is carried as it is
    (it cannot be traded) while the rest of the book is repicked. "Can no
    longer price" includes a store that no longer holds the close the holding
    was marked at, on its own date (a withdrawn or nulled row): an earlier
    close is a different price, so the holding is held at its mark instead.

    **Every amount is in the book currency** (``closes.book_currency``): a
    holding is valued ``shares * native price * rate`` and a pick is sized
    ``floor(target / (native close * rate))``, the rate read on ``iso``, the
    valuation date, whatever the date of the close (``book_rate``, the rule
    the books are valued by). A candidate whose currency is unresolved or whose rate is
    unavailable is not in the draw; a holding in either condition is carried
    at its recorded mark (``CoinFlipHolding.mark_value``) like one the store
    cannot price, and ``frozen`` records why, in the broker's vocabulary, with
    the holding as it was when it first froze (it may have been bought earlier
    in the same run, so the run's starting state need not hold it):
    ``NO_PRICE_DATA``, ``CURRENCY_UNRESOLVED`` (including a ticker that now
    resolves to a currency other than the one its mark was recorded in) or
    ``NO_FX_RATE``.
    """
    carried: dict[str, CoinFlipHolding] = {}
    carried_value = 0.0
    liquid = cash
    for ticker in sorted(holdings):
        h = holdings[ticker]
        base = closes.at(ticker, h.mark_date)
        now = closes.at(ticker, iso)
        reason: str | None = None
        rate: float | None = None
        # The mark must still be in the store on its own date: a row withdrawn
        # since would make the last close before it the base, a different
        # price, and mis-value the holding by the move between the two dates.
        if base is None or base[0] != h.mark_date or now is None or base[1] <= 0:
            reason = NO_PRICE_DATA
        elif closes.currency(ticker) != h.currency:
            reason = CURRENCY_UNRESOLVED
        else:
            rate, reason = closes.rate(h.currency, iso)
        if reason is not None:
            frozen.setdefault(ticker, (reason, h))
            carried[ticker] = h
            carried_value += h.mark_value
            continue
        assert base is not None and now is not None and rate is not None
        price = h.mark_close * now[1] / base[1]
        if ticker in excluded:
            carried[ticker] = CoinFlipHolding(h.shares, now[0], price, h.currency, rate)
            carried_value += h.shares * price * rate
        else:
            liquid += h.shares * price * rate
    total = liquid + carried_value

    candidates = sorted(
        t
        for t in set(universe)
        if t not in excluded and t not in carried and closes.priced(t, iso) is not None
    )
    k = min(max(max_positions, 0), len(candidates))
    picks = random.Random(make_seed(agent_id, iso)).sample(candidates, k)
    weight = 1.0 / max(max_positions, 1)
    new: dict[str, CoinFlipHolding] = dict(carried)
    spent = 0.0
    for ticker in sorted(picks):
        quote = closes.priced(ticker, iso)
        assert quote is not None
        mark_date, mark_close, ccy, rate = quote
        if mark_close <= 0:
            continue
        shares = math.floor(liquid * weight / (mark_close * rate))
        if shares <= 0:
            continue
        new[ticker] = CoinFlipHolding(shares, mark_date, mark_close, ccy, rate)
        spent += shares * mark_close * rate
    return CoinFlipState(
        date=iso, portfolio_value=total, cash=liquid - spent, holdings=new
    )


def _excluded(
    agent_id: str, symbols: Collection[str], concerns: list[str]
) -> set[str]:
    """Symbols the instrument registry marks; a registry that fails closed
    marks them all, and that is a concern."""
    from engine import instrument_status

    marked, problem = instrument_status.statuses(sorted(symbols))
    if problem is not None:
        concerns.append(
            f"coinflip {agent_id}: {problem} — failing closed, no symbol is "
            f"a candidate and every holding is carried untraded."
        )
    return set(marked)


def _row(state: CoinFlipState, currency: str) -> dict:
    return {
        "date": state.date,
        "portfolio_value": state.portfolio_value,
        "cash": state.cash,
        "positions_value": state.portfolio_value - state.cash,
        "currency": currency,
    }


def _unpriceable(closes: _Closes, universe: Collection[str], iso: str) -> str | None:
    """None when some ticker of ``universe`` can be drawn on ``iso``;
    otherwise why none can, counted by reason (``"NO_FX_RATE x3, ..."``).

    The instrument registry plays no part: a registry that fails closed
    carries every holding untraded, which liquidates nothing.
    """
    why: Counter[str] = Counter()
    for t in sorted(universe):
        if closes.at(t, iso) is None:
            why[NO_PRICE_DATA] += 1
            continue
        rate, reason = closes.rate(closes.currency(t), iso)
        if rate is None:
            why[reason or NO_FX_RATE] += 1
            continue
        return None
    return ", ".join(f"{r} x{n}" for r, n in sorted(why.items())) or "no ticker at all"


@dataclass(frozen=True)
class _Stop:
    """Where a run stopped short of ``to_date``: the first date on which
    nothing in the universe could be drawn, and why (``_unpriceable``)."""

    date: str
    why: str


def _stop_concern(
    agent_id: str, universe_size: int, stop: _Stop, kept_through: str | None
) -> str:
    """The ``[WARN]`` concern for a run the empty-draw guard stopped.

    ``kept_through`` is the last date written, or None when the stop fell on
    the first new date and nothing was.
    """
    outcome = (
        f"advanced through {kept_through} only, and the state is kept there"
        if kept_through is not None
        else "not advanced"
    )
    return (
        f"coinflip {agent_id}: no priceable candidate in its universe of "
        f"{universe_size} ticker(s) on {stop.date} ({stop.why}; a missing "
        f"universe file resolves to its bare name); {outcome}, the book is not "
        f"sold to cash. Fix the agent's universe or the store's rates; the "
        f"next run resumes from the state."
    )


def _run(
    agent_id: str,
    state: CoinFlipState,
    to_date: date,
    *,
    closes: _Closes,
    universe: list[str],
    excluded: Collection[str],
    max_positions: int,
    frozen: dict[str, tuple[str, CoinFlipHolding]],
) -> tuple[list[CoinFlipState], _Stop | None]:
    """Every daily state after ``state.date`` through ``to_date``, stopping
    before the first date with no drawable candidate.

    **The empty-draw guard runs on every date** (round-3 review,
    2026-10-06). Stepping such a date would sell the whole book to cash and
    draw nothing. It used to be checked on the first new date only, on the
    argument that priceability only grows with the date, which stopped being
    true with FX: a rate can be zero or withdrawn on a later date. The run
    returns the states before that date and the ``_Stop``; the caller keeps
    them and says where and why it stopped.
    """
    out: list[CoinFlipState] = []
    for d in _daterange(date.fromisoformat(state.date) + timedelta(days=1), to_date):
        why = _unpriceable(closes, universe, d.isoformat())
        if why is not None:
            return out, _Stop(d.isoformat(), why)
        state = _step(
            agent_id,
            state.holdings,
            state.cash,
            d.isoformat(),
            closes=closes,
            universe=universe,
            excluded=excluded,
            max_positions=max_positions,
            frozen=frozen,
        )
        out.append(state)
    return out, None


def init_coin_flip_state(
    agent_id: str,
    tickers: list[str],
    max_positions: int,
    on: date,
    value: float,
    currency: str,
) -> CoinFlipState:
    """A coin flip worth ``value`` (in ``currency``, the series' book
    currency) in cash, repicked at ``on``'s close.

    The start of a fresh path (``value`` = initial capital at day one) and the
    plan 1.6 migration (``value`` = the last published row's value at its
    date): in both, the first repick happens on the start date itself, so
    there is no flat cash day.
    """
    closes = _Closes(tickers, currency)
    concerns: list[str] = []
    excluded = _excluded(agent_id, tickers, concerns)
    for c in concerns:
        print(f"  [WARN] {c}")
    return _step(
        agent_id,
        {},
        value,
        on.isoformat(),
        closes=closes,
        universe=tickers,
        excluded=excluded,
        max_positions=max_positions,
        frozen={},
    )


def compute_coin_flip(
    agent_id: str,
    tickers: list[str],
    currency: str,
    max_positions: int,
    from_date: date,
    to_date: date,
) -> list[dict]:
    """A fresh coin-flip path from ``from_date`` (initial capital) to ``to_date``.

    The stateless view of the same daily step ``advance_coin_flip`` persists;
    the session never calls this on a published series (it advances the
    state). Returns ``[]`` when no ticker in the universe has any close — "no
    line to draw".

    **The series is invariant to the scale its prices are quoted in, from one
    advance to the next** (plan 1.6, 2026-10-05). The bt pipeline this
    replaced was not: ``bt.Backtest`` rounded share counts down at the absolute
    price, so normalising the store from pence to pounds on 2026-08-07 moved
    this control by up to 3.79% on a single day (``goldfinger``, 2026-07-31)
    with no return changed, and every later corporate-action rescale (JMAT.L
    x1.333, APH /2, AVB x0.358) moved every row recomputed after it. Now a
    holding is valued by the ratio of two closes read from the same store
    (``CoinFlipHolding``), so a constant rescale of its history cancels. What
    stays scale-dependent, by design, is the repick itself: whole shares are
    sized at the day's absolute close, so the cash residue of a *new* pick
    depends on the scale it was bought at — a control that cannot hold a
    fraction of a share is more honest about what a random trader could
    actually have done. That residue is fixed the day it is bought and never
    recomputed.
    """
    concerns: list[str] = []
    states = _fresh_path(
        agent_id, tickers, currency, max_positions, from_date, to_date, concerns
    )
    for c in concerns:
        print(f"  [WARN] {c}")
    return [_row(st, currency) for st in states]


def _fresh_path(
    agent_id: str,
    tickers: list[str],
    currency: str,
    max_positions: int,
    from_date: date,
    to_date: date,
    concerns: list[str],
) -> list[CoinFlipState]:
    """Every daily state from ``from_date`` (initial capital, repicked that
    day) through ``to_date``; ``[]`` when no ticker has any close.

    The first day starts from cash, so it has no book to sell and is not
    guarded; every later day is (``_run``), and a stop is one concern."""
    closes = _Closes(tickers, currency)
    if not closes.has_any() or to_date < from_date:
        return []
    excluded = _excluded(agent_id, tickers, concerns)
    frozen: dict[str, tuple[str, CoinFlipHolding]] = {}
    first = _step(
        agent_id,
        {},
        _initial(),
        from_date.isoformat(),
        closes=closes,
        universe=tickers,
        excluded=excluded,
        max_positions=max_positions,
        frozen=frozen,
    )
    rest, stop = _run(
        agent_id,
        first,
        to_date,
        closes=closes,
        universe=tickers,
        excluded=excluded,
        max_positions=max_positions,
        frozen=frozen,
    )
    states = [first] + rest
    if stop is not None:
        concerns.append(
            _stop_concern(agent_id, len(set(tickers)), stop, states[-1].date)
        )
    return states


def advance_coin_flip(
    agent_id: str,
    tickers: list[str],
    currency: str,
    max_positions: int,
    series_path: Path,
    from_date: date,
    to_date: date,
) -> CoinFlipAdvance:
    """Advance one agent's coin flip from its persisted state to ``to_date``.

    **The only writer of ``coinflip.json`` and of its state**, and it writes
    them in that order: the series, then the state. Both the weekday session
    and the weekend refresh reach it through ``build_all_baselines``.

    - **Fresh** — no series (or an empty one) and no state: a new path from
      ``from_date`` at initial capital.
    - **Advance** — the state's date equals the series' last date, and its
      value equals that row's value: new dates only, ``(state.date,
      to_date]``, are appended. No published row is recomputed, whatever the
      universe or the store now say. A same-day re-run appends nothing and
      writes nothing.
    - **Refuse** — a series without a state, a state without a series, an
      unreadable state, or a state that does not chain from the last row (the
      shape a run killed between the two writes leaves): nothing is written,
      the series is not advanced (there is no recompute from day one to fall
      back on) and one ``[WARN]`` concern prints, naming the recovery
      (``COINFLIP_REINIT_REMEDY``: a re-init committed on its own, disclosed
      as the seam it is). The session lifts it into a ``Concerns:`` trailer,
      ``build_all_baselines`` counts it, and ``check_session_freshness`` sees
      the series fall behind the snapshots.
    - **Stop, empty draw** — a new date on which nothing in the universe can
      be drawn (no close, no resolvable currency, or no rate into
      ``currency`` on that date; a missing universe file resolves to its bare
      name): stepping it would sell the book to cash and draw nothing. The
      advance stops before it: the dates before it are appended and the state
      is persisted at the last of them (nothing is written when it is the
      first new date), and one concern names the agent, the date and the
      reasons (``_run``, checked on every date since 2026-10-06, not only the
      first). The instrument registry plays no part in this test: a registry
      that fails closed carries every holding untraded, which liquidates
      nothing.

    A holding whose file is gone, or whose store no longer holds a close dated
    its mark (truncated, or that row withdrawn), or whose currency no longer
    resolves (or whose rate into ``currency`` is unavailable), is held at its
    recorded mark and kept in the book, with one concern naming the agent, the
    ticker and the condition (``NO_PRICE_DATA``, ``CURRENCY_UNRESOLVED``,
    ``NO_FX_RATE``). Every amount is in ``currency`` (``_step``).
    """
    state_path = coin_flip_state_path(series_path)
    name = f"{series_path.parent.name}/{series_path.name}"
    concerns: list[str] = []

    def done(appended: int) -> CoinFlipAdvance:
        for c in concerns:
            print(f"  [WARN] {c}")
        return CoinFlipAdvance(appended, concerns)

    try:
        series: list[dict] = (
            json.loads(series_path.read_text()) if series_path.exists() else []
        )
    except ValueError as exc:
        concerns.append(f"{name} is unreadable ({exc}); not advanced.")
        return done(0)

    if not state_path.exists():
        if series:
            concerns.append(
                f"{name}: {len(series)} published row(s) but no state at "
                f"{state_path.parent.name}/{state_path.name}; the coin flip is "
                f"not advanced (it is never recomputed from day one). "
                f"{COINFLIP_REINIT_REMEDY}"
            )
            return done(0)
        states = _fresh_path(
            agent_id, tickers, currency, max_positions, from_date, to_date, concerns
        )
        if not states:
            if not series_path.exists():
                _write_json(series_path, [])
            return done(0)
        _write_json(series_path, [_row(st, currency) for st in states])
        write_coin_flip_state(state_path, states[-1], agent_id)
        return done(len(states))

    try:
        state = load_coin_flip_state(state_path)
    except ValueError as exc:
        concerns.append(
            f"{name}: state unreadable ({exc}); not advanced. {COINFLIP_REINIT_REMEDY}"
        )
        return done(0)
    if not series:
        concerns.append(
            f"{name}: a state dated {state.date} but no published row; not advanced."
        )
        return done(0)
    last = series[-1]
    if last.get("date") != state.date or last.get("portfolio_value") != state.portfolio_value:
        concerns.append(
            f"{name}: the state ({state.date}, {state.portfolio_value}) does not "
            f"chain from the last published row ({last.get('date')}, "
            f"{last.get('portfolio_value')}); not advanced, nothing recomputed. "
            f"{COINFLIP_REINIT_REMEDY}"
        )
        return done(0)
    if to_date.isoformat() <= state.date:
        return done(0)

    universe = sorted(set(tickers))
    closes = _Closes(set(universe) | set(state.holdings), currency)
    excluded = _excluded(agent_id, set(universe) | set(state.holdings), concerns)
    frozen: dict[str, tuple[str, CoinFlipHolding]] = {}
    states, stop = _run(
        agent_id,
        state,
        to_date,
        closes=closes,
        universe=universe,
        excluded=excluded,
        max_positions=max_positions,
        frozen=frozen,
    )
    for ticker, (reason, h) in sorted(frozen.items()):
        if reason == NO_PRICE_DATA:
            why = (
                f"has no close dated its mark {h.mark_date} in the store (file "
                f"gone, truncated, or that row withdrawn)"
            )
        elif reason == CURRENCY_UNRESOLVED:
            why = (
                f"resolves to no quote currency matching its recorded "
                f"{h.currency} (now {closes.currency(ticker)})"
            )
        else:
            why = f"has no {h.currency}->{currency} rate on a date it was valued"
        concerns.append(
            f"coinflip {agent_id}: {ticker} {reason} — {why}; held at its "
            f"recorded mark of {h.mark_date} ({h.shares} x {h.mark_close:g} "
            f"{h.currency} at {h.mark_rate:g} = {h.mark_value:.2f} {currency}) "
            f"and kept in the book."
        )
    if stop is not None:
        concerns.append(
            _stop_concern(
                agent_id, len(universe), stop, states[-1].date if states else None
            )
        )
    if not states:
        return done(0)
    _write_json(series_path, series + [_row(s, currency) for s in states])
    write_coin_flip_state(state_path, states[-1], agent_id)
    return done(len(states))


def compute_global_reference(
    from_date: date, to_date: date, *, closes: Mapping[str, float] | None = None
) -> list[dict]:
    """€10k buy-and-hold of MSCI World, the site's global reference line."""
    return compute_passive_benchmark(
        get_config().global_reference, from_date, to_date, closes=closes
    )


def _write_json(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


#: Relative tolerance for "the store's mark/base ratio equals the recorded
#: one". A constant rescale of the whole history (units, a restated split)
#: cancels in the ratio, but not bit-exactly (x3 moves 123.45/100.1 by an
#: ulp, ~1e-16). 1e-12 clears that by four orders and sits six below the
#: smallest revision that moves a 10k point by a cent (1e-6).
RATIO_REL_TOL = 1e-12

#: The fields a benchmark row records about the closes it was priced from.
MARK_FIELDS = ("mark_date", "mark_close", "base_date", "base_close")


@dataclass(frozen=True)
class MergeCounts:
    """What one merge (or a whole build) did with each computed row.

    ``appended`` is a new date. Every other field is a published date the
    recomputation disagrees with, classified by ``merge_baseline_series``;
    the published row is kept in every case. Only ``concern`` is a finding.
    """

    appended: int = 0
    stale_mark: int = 0
    rescaled: int = 0
    concern: int = 0
    unclassified: int = 0

    def __add__(self, other: "MergeCounts") -> "MergeCounts":
        return MergeCounts(
            *(getattr(self, f.name) + getattr(other, f.name) for f in fields(self))
        )

    @property
    def mismatched(self) -> int:
        """Published dates the recomputation disagreed with, all classes."""
        return (
            self.stale_mark
            + self.rescaled
            + self.concern
            + self.unclassified
        )


#: The expected classes, each printed as one ``[INFO]`` summary line by
#: ``build_all_baselines``, in this order, with what it means.
EXPECTED_CLASSES: tuple[tuple[str, str], ...] = (
    (
        "stale_mark",
        "published point(s) forward-filled a close that landed later; the "
        "recorded marks still hold in the store",
    ),
    (
        "rescaled",
        "published point(s) whose recorded closes were rescaled in the store "
        "with their ratio unchanged",
    ),
    (
        "unclassified",
        "legacy published point(s) with no entry in their marks sidecar differ "
        "from a recomputation",
    ),
)


def marks_sidecar_path(path: Path) -> Path:
    """``benchmark.json`` -> ``benchmark_marks.json``, beside it."""
    return path.with_name(f"{path.stem}_marks.json")


def _load_marks_sidecar(path: Path) -> tuple[dict[str, dict] | None, str | None]:
    """``({date: marks}, None)`` from the sidecar beside ``path``, or
    ``(None, "missing")`` / ``(None, "unreadable")``.

    **A missing or unreadable sidecar fails toward the concern** (review fix
    3, 2026-10-05): it used to read as ``{}``, so every mismatched legacy row
    became ``unclassified`` ([INFO], not a concern) — the guard failing open
    on the one file that classifies those rows. The merge calls this only for
    a series that has legacy rows, and turns each of their mismatches into a
    concern; an unreadable sidecar is also a concern of its own (it prints
    here, and the merge counts it), while a missing one is silent until a
    legacy row mismatches. Nothing raises.
    """
    sidecar = marks_sidecar_path(path)
    if not sidecar.exists():
        return None, "missing"
    try:
        rows = json.loads(sidecar.read_text())
        return {r["date"]: {k: r[k] for k in MARK_FIELDS} for r in rows}, None
    except (ValueError, TypeError, KeyError) as exc:
        print(
            f"  [WARN] {sidecar.parent.name}/{sidecar.name} is unreadable "
            f"({exc.__class__.__name__}); legacy rows of {path.name} cannot be "
            f"classified, so each one that differs from its recomputation is a "
            f"concern. The sidecar must be repaired "
            f"(scripts/derive_legacy_benchmark_marks.py)."
        )
        return None, "unreadable"


def _ratio_holds(marks: dict, closes: Mapping[str, float] | None) -> bool:
    """Does the store still price the recorded mark/base ratio?

    A recorded close the store no longer holds cannot be confirmed, so it
    does not hold: an unconfirmable mark fails toward the concern.
    """
    if closes is None:
        return False
    mark = closes.get(marks["mark_date"])
    base = closes.get(marks["base_date"])
    if mark is None or base is None or base == 0 or marks["base_close"] == 0:
        return False
    recorded = marks["mark_close"] / marks["base_close"]
    return math.isclose(mark / base, recorded, rel_tol=RATIO_REL_TOL, abs_tol=0.0)


def _has_later_close(
    marks: dict, row_date: str, dates: Sequence[str] | None
) -> bool:
    """Does the store now hold a close in (mark_date, row_date]?

    ``dates`` is the store's close dates, sorted (``merge_baseline_series``
    sorts them once per series), so this is one bisect, not a scan of every
    close for every row.
    """
    if dates is None or marks["mark_date"] >= row_date:
        return False
    i = bisect.bisect_right(dates, marks["mark_date"])
    return i < len(dates) and dates[i] <= row_date


def _classify(
    published: dict,
    computed: dict,
    *,
    sidecar: dict[str, dict] | None,
    closes: Mapping[str, float] | None,
    dates: Sequence[str] | None,
) -> str | None:
    """The class of a published row against its recomputation, or None if equal.

    ``sidecar`` is None when the series' marks sidecar is missing or
    unreadable: a mismatched legacy row is then a concern, never
    ``unclassified``."""
    if "mark_date" in published:
        if published == computed:
            return None
        marks = {k: published[k] for k in MARK_FIELDS}
    else:
        # Legacy row: compare on value and currency only, so that adding the
        # mark fields to the recomputation refuses nothing by itself.
        if (
            published.get("portfolio_value") == computed.get("portfolio_value")
            and published.get("currency") == computed.get("currency")
        ):
            return None
        if sidecar is None:
            return "concern"
        marks = sidecar.get(published["date"])
        if marks is None:
            return "unclassified"
    if marks["mark_date"] != marks["base_date"] and not _ratio_holds(marks, closes):
        return "concern"
    if _has_later_close(marks, published["date"], dates):
        return "stale_mark"
    return "rescaled"


def merge_baseline_series(
    path: Path,
    computed: list[dict],
    *,
    restate: bool = False,
    closes: Mapping[str, float] | None = None,
) -> MergeCounts:
    """Append-or-keep merge of a freshly computed series onto a baseline file,
    classifying every published point the recomputation disagrees with.

    Reaches the same outcome as ``PortfolioManager.add_snapshot`` on the other
    curve plotted on the same dossier chart — a published point is immutable
    to a later run. A date not yet on disk is appended; a date already on
    disk is kept exactly as published, whatever ``computed`` now says.
    ``restate=True`` is the explicit, one-time escape hatch: every date in
    ``computed`` overwrites its on-disk counterpart, used only for a
    deliberate, publicly logged restatement (and counted in no class). It is
    the only restatement mechanism here: which rows a restatement writes —
    a whole series' published dates, or one dated scope's — is decided by
    ``_restatement_plan``, which hands this merge exactly those rows and
    validates every scope entry before the first write.

    **What changed (plan 1.5, 2026-10-04): a mismatch is classified, not
    refused as one undifferentiated count.** The old docstring argued that
    any mismatch on a published date is the signal that the store changed
    retroactively. Measured on the 2026-10-01 session, 1,830 refusals carried
    11 genuine revisions; the rest were points that forward-filled a close
    which landed later, and coin-flip path recomputes (the coin flip has since
    left this merge, plan 1.6). A count dominated by
    the expected case is a guard nobody reads. Classes, per published date:

    - ``concern`` — **the only finding.** The row's recorded
      ``mark_close/base_close`` differs from the store's close on the same
      two dates (relative ``RATIO_REL_TOL``), or the store no longer holds
      one of them: a close the row was priced from was revised. A row with
      ``mark_date == base_date`` is never a concern — its value is the
      initial capital by construction. Printed as one ``[WARN]`` per row,
      naming its remedy: the dated scope ``"<agent>/<kind>@<date>"`` with a
      METHODOLOGY changelog anchor, from a human-authored ``[restate]``
      commit.
    - ``stale_mark`` — the ratio holds, ``mark_date`` is before the row's
      date, and the store now holds a close in ``(mark_date, date]``: the
      point forward-filled a close that had not landed yet. The published
      row is right for what it saw and mismatches forever; no horizon makes
      it drift.
    - ``rescaled`` — the ratio holds and no later close landed, yet the row
      differs: the store's closes were rescaled (units, a restated split)
      with their ratio unchanged, which a ratio series cancels.
    - ``unclassified`` — a legacy row (no mark fields) with no entry in a
      readable marks sidecar. A legacy row of a series whose sidecar is
      missing or unreadable is a ``concern`` instead, and an unreadable
      sidecar is one concern of its own (``_load_marks_sidecar``).

    **The coin flip does not come through here** (plan 1.6, 2026-10-05): it
    is advanced from a persisted state over new dates only
    (``advance_coin_flip``), so it has no recomputation to classify. The
    ``path_recompute`` class this merge carried for it until then is gone.

    **Where the recorded marks come from.** A row written since plan 1.5
    carries them (``compute_passive_benchmark``). A legacy row is never
    rewritten to add them (the append-only gate freezes it byte for byte);
    its marks were derived from the writer's own store by
    ``scripts/derive_legacy_benchmark_marks.py`` into a dated sidecar beside
    the series (``benchmark_marks.json``, ``msci_world_marks.json``). Row
    fields are read first, then the sidecar. A legacy row compares on
    ``portfolio_value`` and ``currency`` only, so adding the fields to the
    recomputation moves nothing by itself.

    Nothing here raises — a session that dies because one benchmark point
    drifted is worse than one that surfaces it. The same posture covers the
    case where ``computed`` is empty but the file already holds history:
    within-range gaps are already forward-filled (see
    ``compute_passive_benchmark``), so an empty ``computed`` against an
    established baseline means a whole ticker file is missing — a persistent
    condition, not a blip — and it prints a ``[WARN]``, unlike a brand-new
    agent's first-ever build, where an empty ``computed`` against no prior
    file is the ordinary "no data yet" case and stays silent.

    Parameters
    ----------
    path:
        Target baseline file (created if it does not exist yet).
    computed:
        Freshly computed series for the full [from_date, to_date] window.
    restate:
        When True, every date overwrites the on-disk row instead of being
        kept. Reserved for a deliberate restatement, called only with the
        rows ``_restatement_plan`` chose.
    closes:
        The store's ``{date: close}`` for the benchmark's ticker. Without it
        no recorded mark can be confirmed, so every mark-bearing mismatch is
        a concern.

    Returns
    -------
    MergeCounts
        Appended dates and the mismatches per class.
    """
    existing: list[dict] = json.loads(path.read_text()) if path.exists() else []
    if not computed and existing:
        print(
            f"  [WARN] {path.name}: computed series is empty against "
            f"{len(existing)} published row(s) — likely a missing OHLCV "
            f"ticker file (within-range gaps are already forward-filled), "
            f"not a transient blip. Keeping the published file as-is."
        )
        return MergeCounts()
    sidecar: dict[str, dict] | None = {}
    sidecar_problem: str | None = None
    if any("mark_date" not in r for r in existing):
        sidecar, sidecar_problem = _load_marks_sidecar(path)
    by_date = {row["date"]: row for row in existing}
    close_dates = sorted(closes) if closes is not None else None
    tally = {f.name: 0 for f in fields(MergeCounts)}
    if sidecar_problem == "unreadable":
        tally["concern"] += 1
    for row in computed:
        date_key = row["date"]
        if date_key not in by_date:
            by_date[date_key] = row
            tally["appended"] += 1
            continue
        if restate:
            by_date[date_key] = row
            continue
        published = by_date[date_key]
        verdict = _classify(
            published, row, sidecar=sidecar, closes=closes, dates=close_dates
        )
        if verdict is None:
            continue
        tally[verdict] += 1
        if verdict == "concern" and "mark_date" not in published and sidecar is None:
            sidecar_name = marks_sidecar_path(path).name
            print(
                f"  [WARN] {path.parent.name}/{path.name}: {date_key} concern — "
                f"a legacy row (no recorded marks) published at "
                f"{published.get('portfolio_value')} differs from its "
                f"recomputation {row.get('portfolio_value')}, and "
                f"{sidecar_name} is {sidecar_problem}, so it cannot be "
                f"classified. The published value was kept. Remedy: restore "
                f"{sidecar_name} (scripts/derive_legacy_benchmark_marks.py), "
                f"then re-run; restate the row only if it is then a concern."
            )
        elif verdict == "concern":
            assert sidecar is not None or "mark_date" in published
            marks = (
                {k: published[k] for k in MARK_FIELDS}
                if "mark_date" in published
                else sidecar[date_key]
            )
            store = closes or {}
            print(
                f"  [WARN] {path.parent.name}/{path.name}: {date_key} concern — "
                f"published from {marks['mark_date']} at {marks['mark_close']} "
                f"over {marks['base_date']} at {marks['base_close']}; the store "
                f"now holds {store.get(marks['mark_date'])} over "
                f"{store.get(marks['base_date'])}: a recorded close was revised. "
                f"The published value was kept. Remedy: restate the row with the "
                f"dated scope \"{path.parent.name}/{path.stem}@{date_key}\" and "
                f"a METHODOLOGY changelog anchor, from a human-authored "
                f"[restate] commit."
            )
    merged = [by_date[d] for d in sorted(by_date)]
    _write_json(path, merged)
    return MergeCounts(**tally)


#: The series kinds a restatement scope may name.
_DATED_KINDS = ("benchmark", "msci_world")

_DATED_FORM = "<agent>/<kind>@<YYYY-MM-DD>"

#: What a refused coin-flip restatement scope says.
_COINFLIP_RESTATE_REFUSED = (
    "the coin flip cannot be restated: it is advanced from a persisted state "
    "over new dates only (plan 1.6, METHODOLOGY #stateful-coinflip-2026-10-05)"
)


@dataclass(frozen=True)
class _ScopeEntry:
    """One parsed restatement scope entry.

    ``agent`` is None for a bare kind (every series of that kind); ``day`` is
    None for a whole series.
    """

    agent: str | None
    kind: str
    day: str | None


def _parse_scope_entry(entry: str, cfg) -> _ScopeEntry:
    """Parse and validate one scope entry: a bare kind, ``"<agent>/<kind>"``
    or ``"<agent>/<kind>@<YYYY-MM-DD>"``. Raises ``ValueError`` for an entry
    that names no restatable series. Reads no file.

    The one place a scope entry is read: the coin-flip refusal, the date, the
    kind, the agent, the ``global``/``msci_world`` pairing and the roster
    benchmark are each checked here once, for every form.
    """
    series, at, day = entry.partition("@")
    if not at and series in _DATED_KINDS:
        return _ScopeEntry(None, series, None)
    if series == "coinflip" or series.endswith("/coinflip"):
        raise ValueError(f"{entry!r}: {_COINFLIP_RESTATE_REFUSED}")
    agent, slash, kind = series.partition("/")
    if not (slash and agent and kind):
        if at:
            raise ValueError(f"{entry!r}: a dated scope reads {_DATED_FORM}")
        raise ValueError(
            f"{entry!r}: unknown restatement scope; a scope entry is one of "
            f"{list(_DATED_KINDS)}, <agent>/<kind> or {_DATED_FORM}"
        )
    if at:
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError
        except ValueError:
            raise ValueError(
                f"{entry!r}: {day!r} is not an ISO date ({_DATED_FORM})"
            ) from None
    if kind not in _DATED_KINDS:
        raise ValueError(
            f"{entry!r}: unknown series kind {kind!r}; a scope takes one of "
            f"{list(_DATED_KINDS)}"
        )
    if agent != "global" and agent not in cfg.trading_roster:
        raise ValueError(f"{entry!r}: unknown agent {agent!r}")
    if (agent == "global") != (kind == "msci_world"):
        raise ValueError(
            f"{entry!r}: msci_world belongs to 'global', benchmark to an agent"
        )
    if agent != "global" and cfg.roster[agent].benchmark is None:
        raise ValueError(
            f"{entry!r}: {agent!r} has no benchmark in the roster, so there is "
            f"no benchmark series to restate"
        )
    return _ScopeEntry(agent, kind, day or None)


def _series_restated(entries: Collection[_ScopeEntry], agent: str, kind: str) -> bool:
    """Does the caller's restatement scope cover this one whole series?

    A scope entry is either a bare kind (`"benchmark"` — every agent's
    passive benchmark — or `"msci_world"`) or a fully-qualified
    `"<agent>/<kind>"` (`"goldfinger/benchmark"`, `"global/msci_world"`). A
    third form, `"<agent>/<kind>@<YYYY-MM-DD>"`, restates ONE published date
    of one series (`_series_restated_dates`) and makes this function answer
    False for it: no other row of that series moves. It exists because the
    rows that genuinely needed restating (priced from provisional bars,
    2026-10-05) sat inside series whose other rows must not move.

    **A restatement is restate-only** (whole-branch review I1, 2026-10-05).
    A covered series has its *published* dates rewritten from the
    recomputation and nothing else: no new date is appended to it or to any
    other series, and no coin flip advances. The routine append runs from a
    call with no scope. Every entry is parsed and validated once,
    by ``_parse_scope_entry``, before the first write.

    **The coin flip cannot be restated** (plan 1.6, 2026-10-05):
    ``build_all_baselines`` refuses a scope naming it, bare or qualified. It is
    a path advanced from a persisted state, and recomputing it over history
    with today's universe and store would both splice a new path under the
    published one and give it look-ahead (a universe chosen later deciding
    earlier picks). The 2026-08-07 coin-flip restatement below is the last
    one there will be.

    This replaced a plain bool, which could only say "restate everything".
    That is not a hypothetical shortcoming: on 2026-08-07 the coin-flip series
    genuinely needed restating onto normalised units, the passive benchmarks
    did not, and the blanket flag moved eight of them anyway — on *fresher
    prices*, not on units, which is precisely the retroactive drift the
    append-only rule exists to refuse. They had to be restored by hand.
    An API that cannot express the intended scope will eventually be used
    outside it.
    """
    return any(
        e.kind == kind and e.day is None and e.agent in (None, agent) for e in entries
    )


def _series_restated_dates(
    entries: Collection[_ScopeEntry], agent: str, kind: str
) -> frozenset[str]:
    """The dates of this one series a dated scope entry names."""
    return frozenset(
        e.day for e in entries if e.day is not None and (e.agent, e.kind) == (agent, kind)
    )


@dataclass(frozen=True)
class _Restatement:
    """One series a restatement rewrites, and the rows it writes."""

    path: Path
    rows: list[dict]


def _restatement_plan(
    entries: Collection[_ScopeEntry], cfg, from_date: date, to_date: date
) -> list[_Restatement]:
    """Every series the scope covers, with the published rows it rewrites.

    Raises ``ValueError`` before anything is written when an entry names no
    published row: a dated entry whose day is not a published date of its
    series, or one the recomputation over ``[from_date, to_date]`` does not
    hold. A covered series' rows are its published dates only, so the merge
    that writes them appends nothing.
    """
    series: list[tuple[str, str, BenchmarkSpec]] = [
        (aid, "benchmark", cfg.roster[aid].benchmark)
        for aid in cfg.trading_roster
        if cfg.roster[aid].benchmark is not None
    ]
    series.append(("global", "msci_world", cfg.global_reference))
    plan: list[_Restatement] = []
    for agent, kind, spec in series:
        whole = _series_restated(entries, agent, kind)
        dated = _series_restated_dates(entries, agent, kind)
        if not whole and not dated:
            continue
        path = cfg.baselines_dir / agent / f"{kind}.json"
        name = f"{agent}/{kind}"
        published = (
            {r["date"] for r in json.loads(path.read_text())} if path.exists() else set()
        )
        unpublished = sorted(dated - published)
        if unpublished:
            raise ValueError(
                f"{name}: restate date(s) {unpublished} not a published date of "
                f"{path.parent.name}/{path.name} — a restatement rewrites published "
                f"rows only, it never appends one"
            )
        computed = {
            r["date"]: r for r in compute_passive_benchmark(spec, from_date, to_date)
        }
        absent = sorted(dated - computed.keys())
        if absent:
            raise ValueError(
                f"{name}: restate date(s) {absent} not in the computed series "
                f"over {from_date}..{to_date} — nothing would be restated"
            )
        targets = (published if whole else dated) & computed.keys()
        if targets:
            plan.append(_Restatement(path, [computed[d] for d in sorted(targets)]))
    return plan


def build_all_baselines(
    universes_by_agent: dict[str, list[str]],
    from_date: date,
    to_date: date,
    max_positions_by_agent: dict[str, int] | None = None,
    *,
    restate_series: Collection[str] | None = None,
    changelog_entry: str | None = None,
) -> MergeCounts:
    """Produce all per-agent baseline files + the global reference file.

    Iterates get_config().trading_roster; agents whose benchmark is None are
    skipped. Append-or-keep: an already-published date is kept as-is (a
    mismatch is classified, ``merge_baseline_series``) and new dates are
    appended.

    **A restatement is restate-only** (whole-branch review I1, 2026-10-05).
    With a non-empty ``restate_series`` (see ``_series_restated`` for the
    scoping rules, and for why this is not a bool) the call writes the
    scoped published rows and nothing else: no new date is appended to any
    series and ``advance_coin_flip`` is not called, so no coin-flip row or
    state is written. Every entry is validated before the first write — a
    dated entry's day must already be a published date of its series, an
    entry must name a series that exists (an agent whose roster
    ``benchmark`` is None has none), and one bad entry refuses the whole
    scope with ``ValueError``. The routine append comes from a later call
    with no scope, so a ``[restate]`` commit carries only what it discloses.

    Missing OHLCV data
    for a brand-new agent (no prior file) yields an empty file — "no line to
    draw" for the site. Missing OHLCV data against an *established* baseline
    is not empty: the old file is kept frozen and a [WARN] is printed by
    ``merge_baseline_series`` (see its docstring for why those two cases
    differ, and for the mismatch classes). Each agent's coin flip is not
    merged: ``advance_coin_flip`` advances it from its persisted state over
    new dates only, and a scope naming it (``"coinflip"`` or
    ``"<agent>/coinflip"``) is refused with ``ValueError`` before anything is
    written.

    **What reaches the session's ``Concerns:`` path.** The session model
    turns printed ``[WARN]`` lines into commit trailers, so only a
    concern prints as one: each benchmark row as it is found, and each coin
    flip that cannot be advanced or holds a ticker the store can no longer
    price, then one aggregate ``[WARN] baselines: N concern(s)`` line whose
    N counts both (``MergeCounts.concern`` in the returned totals; review
    M2). Every expected class (``stale_mark``, ``rescaled``,
    ``unclassified``)
    prints exactly one ``[INFO] … not a concern`` summary line across every
    file this build merged, when non-zero, and never a per-row line — the
    2026-10-01 session printed 1,830 per-row warnings for 11 real
    revisions. Returns the totals.

    **A non-empty ``restate_series`` requires ``changelog_entry``** — the
    anchor of the METHODOLOGY.md entry disclosing what moves and why, verified
    to resolve (``engine.disclosure``). This is the third path that can move a
    published number, and it was the last one still ungated:
    ``restate_valuations.py`` and ``restate_bundles.py`` grew the same
    precondition on 2026-08-07, but a baseline series can only be restated
    from Python, so there was no ``--changelog-entry`` flag to require. The
    routine session call passes no scope and is unaffected.
    """
    cfg = get_config()
    entries = [_parse_scope_entry(e, cfg) for e in sorted(restate_series or ())]
    if restate_series:
        require_changelog_entry(
            changelog_entry,
            what=f"Restating baseline series {sorted(restate_series)}",
        )
        plan = _restatement_plan(entries, cfg, from_date, to_date)
        for item in plan:
            merge_baseline_series(item.path, item.rows, restate=True)
            print(
                f"  [INFO] baselines: restated {len(item.rows)} published row(s) "
                f"of {item.path.parent.name}/{item.path.name}; nothing appended."
            )
        return MergeCounts()
    max_positions_by_agent = max_positions_by_agent or {}
    baselines_dir = cfg.baselines_dir
    totals = MergeCounts()
    coin_concerns = 0
    for agent_id in cfg.trading_roster:
        spec = cfg.roster[agent_id].benchmark
        if spec is None:
            continue
        agent_dir = baselines_dir / agent_id
        closes = _benchmark_closes(spec)  # read once: priced from, classified against
        totals += merge_baseline_series(
            agent_dir / "benchmark.json",
            compute_passive_benchmark(spec, from_date, to_date, closes=closes),
            closes=closes,
        )

        tickers = universes_by_agent.get(agent_id, [])
        max_pos = max_positions_by_agent.get(agent_id, 5)
        coin = advance_coin_flip(
            agent_id=agent_id,
            tickers=tickers,
            currency=spec.currency,
            max_positions=max_pos,
            series_path=agent_dir / "coinflip.json",
            from_date=from_date,
            to_date=to_date,
        )
        coin_concerns += len(coin.concerns)
        totals += MergeCounts(appended=coin.appended, concern=len(coin.concerns))

    ref_closes = _benchmark_closes(cfg.global_reference)
    totals += merge_baseline_series(
        baselines_dir / "global" / "msci_world.json",
        compute_global_reference(from_date, to_date, closes=ref_closes),
        closes=ref_closes,
    )

    for name, meaning in EXPECTED_CLASSES:
        count = getattr(totals, name)
        if count:
            print(f"  [INFO] baselines: {count} {name} — {meaning}; not a concern.")
    if totals.concern:
        parts = []
        if totals.concern - coin_concerns:
            parts.append(
                f"{totals.concern - coin_concerns} benchmark point(s) priced from "
                f"a close the store has since revised (published values kept)"
            )
        if coin_concerns:
            parts.append(f"{coin_concerns} coin flip concern(s)")
        print(
            f"  [WARN] baselines: {totals.concern} concern(s) — "
            f"{'; '.join(parts)}; each [WARN] above names its remedy. "
            f"{totals.appended} new point(s) appended."
        )
    return totals


def _benchmark_closes(spec: BenchmarkSpec) -> dict[str, float] | None:
    """The store's closes for a benchmark, or None for one that reads no price."""
    if spec.ticker == "EUR_CASH_FLAT":
        return None
    return _load_ohlcv(spec.ticker)
