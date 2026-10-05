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
"""

from __future__ import annotations

import bisect
import json
import math
import random
from dataclasses import dataclass, fields
from datetime import date, timedelta
from pathlib import Path
from typing import Collection, Iterator, Mapping

from engine.config import BenchmarkSpec, get_config
from engine.disclosure import require_changelog_entry
from engine.selectors.seeding import make_seed


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

#: Version of the persisted coin-flip state document.
COINFLIP_STATE_SCHEMA = 1


@dataclass(frozen=True)
class CoinFlipHolding:
    """One position of the coin flip, recorded scale-invariantly.

    ``mark_close`` is what one share was worth when the holding was last
    marked, and ``mark_date`` the store date of the close that mark was read
    from. The next valuation is ``shares * mark_close * close(d) /
    close(mark_date)``, both closes read from the *current* store, so a
    constant rescale of the symbol's history (a unit change, a restated split)
    cancels. ``mark_date`` is the close's own date rather than the state date
    so that a close landing late for the state date is credited to the next
    row instead of being lost from the path.
    """

    shares: int
    mark_date: str
    mark_close: float


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
                "mark_date": h.mark_date,
                "mark_close": h.mark_close,
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
        if doc.get("schema") != COINFLIP_STATE_SCHEMA:
            raise ValueError(f"schema is not {COINFLIP_STATE_SCHEMA}")
        date.fromisoformat(doc["date"])
        holdings = {}
        for ticker, h in doc["holdings"].items():
            date.fromisoformat(h["mark_date"])
            holdings[ticker] = CoinFlipHolding(
                int(h["shares"]), h["mark_date"], float(h["mark_close"])
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
    """The current store's closes, with the last close on or before a date."""

    def __init__(self, tickers: Collection[str]) -> None:
        self._dates: dict[str, list[str]] = {}
        self._values: dict[str, list[float]] = {}
        for t in tickers:
            closes = _load_ohlcv(t)
            if closes:
                ordered = sorted(closes)
                self._dates[t] = ordered
                self._values[t] = [closes[d] for d in ordered]

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
    frozen: set[str],
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
    """
    carried: dict[str, CoinFlipHolding] = {}
    carried_value = 0.0
    liquid = cash
    for ticker in sorted(holdings):
        h = holdings[ticker]
        base = closes.at(ticker, h.mark_date)
        now = closes.at(ticker, iso)
        # The mark must still be in the store on its own date: a row withdrawn
        # since would make the last close before it the base, a different
        # price, and mis-value the holding by the move between the two dates.
        if base is None or base[0] != h.mark_date or now is None or base[1] <= 0:
            frozen.add(ticker)
            carried[ticker] = h
            carried_value += h.shares * h.mark_close
            continue
        price = h.mark_close * now[1] / base[1]
        if ticker in excluded:
            carried[ticker] = CoinFlipHolding(h.shares, now[0], price)
            carried_value += h.shares * price
        else:
            liquid += h.shares * price
    total = liquid + carried_value

    candidates = sorted(
        t
        for t in set(universe)
        if t not in excluded and t not in carried and closes.at(t, iso) is not None
    )
    k = min(max(max_positions, 0), len(candidates))
    picks = random.Random(make_seed(agent_id, iso)).sample(candidates, k)
    weight = 1.0 / max(max_positions, 1)
    new: dict[str, CoinFlipHolding] = dict(carried)
    spent = 0.0
    for ticker in sorted(picks):
        mark = closes.at(ticker, iso)
        assert mark is not None
        if mark[1] <= 0:
            continue
        shares = math.floor(liquid * weight / mark[1])
        if shares <= 0:
            continue
        new[ticker] = CoinFlipHolding(shares, mark[0], mark[1])
        spent += shares * mark[1]
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


def _run(
    agent_id: str,
    state: CoinFlipState,
    to_date: date,
    *,
    closes: _Closes,
    universe: list[str],
    excluded: Collection[str],
    max_positions: int,
    frozen: set[str],
) -> list[CoinFlipState]:
    """Every daily state after ``state.date`` through ``to_date``."""
    out: list[CoinFlipState] = []
    for d in _daterange(date.fromisoformat(state.date) + timedelta(days=1), to_date):
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
    return out


def init_coin_flip_state(
    agent_id: str,
    tickers: list[str],
    max_positions: int,
    on: date,
    value: float,
) -> CoinFlipState:
    """A coin flip worth ``value`` in cash, repicked at ``on``'s close.

    The start of a fresh path (``value`` = initial capital at day one) and the
    plan 1.6 migration (``value`` = the last published row's value at its
    date): in both, the first repick happens on the start date itself, so
    there is no flat cash day.
    """
    closes = _Closes(tickers)
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
        frozen=set(),
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
    states = _fresh_path(agent_id, tickers, max_positions, from_date, to_date, concerns)
    for c in concerns:
        print(f"  [WARN] {c}")
    return [_row(st, currency) for st in states]


def _fresh_path(
    agent_id: str,
    tickers: list[str],
    max_positions: int,
    from_date: date,
    to_date: date,
    concerns: list[str],
) -> list[CoinFlipState]:
    """Every daily state from ``from_date`` (initial capital, repicked that
    day) through ``to_date``; ``[]`` when no ticker has any close."""
    closes = _Closes(tickers)
    if not closes.has_any() or to_date < from_date:
        return []
    excluded = _excluded(agent_id, tickers, concerns)
    frozen: set[str] = set()
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
    return [first] + _run(
        agent_id,
        first,
        to_date,
        closes=closes,
        universe=tickers,
        excluded=excluded,
        max_positions=max_positions,
        frozen=frozen,
    )


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
      back on) and one ``[WARN]`` concern prints. The session lifts it into a
      ``Concerns:`` trailer, and ``check_session_freshness`` sees the series
      fall behind the snapshots.

    A holding whose file is gone, or whose store no longer holds a close dated
    its mark (truncated, or that row withdrawn), is held at its recorded mark
    and kept in the book, with one concern naming the agent and the ticker.
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
                f"Initialise it with scripts/init_coinflip_state.py."
            )
            return done(0)
        states = _fresh_path(
            agent_id, tickers, max_positions, from_date, to_date, concerns
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
        concerns.append(f"{name}: state unreadable ({exc}); not advanced.")
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
            f"{last.get('portfolio_value')}); not advanced, nothing recomputed."
        )
        return done(0)
    if to_date.isoformat() <= state.date:
        return done(0)

    universe = sorted(set(tickers))
    closes = _Closes(set(universe) | set(state.holdings))
    excluded = _excluded(agent_id, set(universe) | set(state.holdings), concerns)
    frozen: set[str] = set()
    states = _run(
        agent_id,
        state,
        to_date,
        closes=closes,
        universe=universe,
        excluded=excluded,
        max_positions=max_positions,
        frozen=frozen,
    )
    for ticker in sorted(frozen):
        h = state.holdings.get(ticker)
        concerns.append(
            f"coinflip {agent_id}: {ticker} has no close dated its mark "
            f"{h.mark_date if h else '?'} in the store (file gone, truncated, or "
            f"that row withdrawn); held at its recorded mark and kept in the book."
        )
    _write_json(series_path, series + [_row(s, currency) for s in states])
    write_coin_flip_state(state_path, states[-1], agent_id)
    return done(len(states))


def compute_global_reference(from_date: date, to_date: date) -> list[dict]:
    """€10k buy-and-hold of MSCI World, the site's global reference line."""
    return compute_passive_benchmark(get_config().global_reference, from_date, to_date)


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
        "legacy published point(s) with no recorded marks differ from a "
        "recomputation",
    ),
)


def marks_sidecar_path(path: Path) -> Path:
    """``benchmark.json`` -> ``benchmark_marks.json``, beside it."""
    return path.with_name(f"{path.stem}_marks.json")


def _load_marks_sidecar(path: Path) -> dict[str, dict]:
    """``{date: marks}`` from the sidecar beside ``path``, or ``{}``.

    An unreadable sidecar warns and classifies nothing (every legacy mismatch
    then reads ``unclassified``): the merge prints rather than raises, and the
    warning is what reaches the session's ``Concerns:`` path.
    """
    sidecar = marks_sidecar_path(path)
    if not sidecar.exists():
        return {}
    try:
        rows = json.loads(sidecar.read_text())
        return {
            r["date"]: {k: r[k] for k in MARK_FIELDS}
            for r in rows
        }
    except (ValueError, TypeError, KeyError) as exc:
        print(
            f"  [WARN] {sidecar.parent.name}/{sidecar.name} is unreadable "
            f"({exc.__class__.__name__}); legacy rows of {path.name} cannot be "
            f"classified — a concern, the sidecar must be repaired."
        )
        return {}


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
    marks: dict, row_date: str, closes: Mapping[str, float] | None
) -> bool:
    """Does the store now hold a close in (mark_date, row_date]?"""
    if closes is None or marks["mark_date"] >= row_date:
        return False
    return any(marks["mark_date"] < d <= row_date for d in closes)


def _classify(
    published: dict,
    computed: dict,
    *,
    sidecar: dict[str, dict],
    closes: Mapping[str, float] | None,
) -> str | None:
    """The class of a published row against its recomputation, or None if equal."""
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
        marks = sidecar.get(published["date"])
        if marks is None:
            return "unclassified"
    if marks["mark_date"] != marks["base_date"] and not _ratio_holds(marks, closes):
        return "concern"
    if _has_later_close(marks, published["date"], closes):
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
    deliberate, publicly logged restatement (and counted in no class).

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
      initial capital by construction. Printed as one ``[WARN]`` per row.
    - ``stale_mark`` — the ratio holds, ``mark_date`` is before the row's
      date, and the store now holds a close in ``(mark_date, date]``: the
      point forward-filled a close that had not landed yet. The published
      row is right for what it saw and mismatches forever; no horizon makes
      it drift.
    - ``rescaled`` — the ratio holds and no later close landed, yet the row
      differs: the store's closes were rescaled (units, a restated split)
      with their ratio unchanged, which a ratio series cancels.
    - ``unclassified`` — a legacy row (no mark fields) with no entry in the
      marks sidecar.

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
        kept. Reserved for a deliberate restatement.
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
    sidecar = _load_marks_sidecar(path) if existing else {}
    by_date = {row["date"]: row for row in existing}
    tally = {f.name: 0 for f in fields(MergeCounts)}
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
        verdict = _classify(published, row, sidecar=sidecar, closes=closes)
        if verdict is None:
            continue
        tally[verdict] += 1
        if verdict == "concern":
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
                f"The published value was kept."
            )
    merged = [by_date[d] for d in sorted(by_date)]
    _write_json(path, merged)
    return MergeCounts(**tally)


def _series_restated(
    restate_series: Collection[str] | None, agent: str, kind: str
) -> bool:
    """Does the caller's restatement scope cover this one series?

    A scope entry is either a bare kind (`"benchmark"` — every agent's
    passive benchmark) or a fully-qualified `"<agent>/<kind>"`
    (`"goldfinger/benchmark"`, `"global/msci_world"`).

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
    append-or-refuse rule exists to refuse. They had to be restored by hand.
    An API that cannot express the intended scope will eventually be used
    outside it.
    """
    if not restate_series:
        return False
    return kind in restate_series or f"{agent}/{kind}" in restate_series


#: What a refused coin-flip restatement scope says.
_COINFLIP_RESTATE_REFUSED = (
    "the coin flip cannot be restated: it is advanced from a persisted state "
    "over new dates only (plan 1.6, METHODOLOGY #stateful-coinflip-2026-10-05)"
)


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
    skipped. Append-or-keep: an already-published date is kept as-is unless
    the series is named in ``restate_series`` (see ``_series_restated`` for
    the scoping rules, and for why this is not a bool). Missing OHLCV data
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
    ``concern`` prints as one: each row as it is found, then one aggregate
    ``[WARN] baselines: N concern(s)`` line; a coin flip that cannot be
    advanced, or holds a ticker the store can no longer price, prints its own
    ``[WARN]`` line. Every expected class (``stale_mark``, ``rescaled``,
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
    refused = sorted(
        s for s in restate_series or () if s == "coinflip" or s.endswith("/coinflip")
    )
    if refused:
        raise ValueError(f"{refused}: {_COINFLIP_RESTATE_REFUSED}")
    if restate_series:
        require_changelog_entry(
            changelog_entry,
            what=f"Restating baseline series {sorted(restate_series)}",
        )
    cfg = get_config()
    max_positions_by_agent = max_positions_by_agent or {}
    baselines_dir = cfg.baselines_dir
    totals = MergeCounts()
    for agent_id in cfg.trading_roster:
        spec = cfg.roster[agent_id].benchmark
        if spec is None:
            continue
        agent_dir = baselines_dir / agent_id
        totals += merge_baseline_series(
            agent_dir / "benchmark.json",
            compute_passive_benchmark(spec, from_date, to_date),
            restate=_series_restated(restate_series, agent_id, "benchmark"),
            closes=_benchmark_closes(spec),
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
        totals += MergeCounts(appended=coin.appended)

    totals += merge_baseline_series(
        baselines_dir / "global" / "msci_world.json",
        compute_global_reference(from_date, to_date),
        restate=_series_restated(restate_series, "global", "msci_world"),
        closes=_benchmark_closes(cfg.global_reference),
    )

    for name, meaning in EXPECTED_CLASSES:
        count = getattr(totals, name)
        if count:
            print(f"  [INFO] baselines: {count} {name} — {meaning}; not a concern.")
    if totals.concern:
        print(
            f"  [WARN] baselines: {totals.concern} concern(s) — a close a "
            f"published benchmark point was priced from has been revised in "
            f"the store (rows above); the published values were kept. "
            f"{totals.appended} new point(s) appended."
        )
    return totals


def _benchmark_closes(spec: BenchmarkSpec) -> dict[str, float] | None:
    """The store's closes for a benchmark, or None for one that reads no price."""
    if spec.ticker == "EUR_CASH_FLAT":
        return None
    return _load_ohlcv(spec.ticker)
