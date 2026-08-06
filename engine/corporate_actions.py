"""Corporate-action detection and position adjustment — stock splits.

Built after commit 4b6b8556 corrected 29,348 rows in the committed OHLCV
store, ~10k of them 11 tickers whose pre-split history had never been
restated. Nothing in the engine handled corporate actions before this
module: a position held through a split kept its pre-split share count
while the store's price became post-split, silently mis-valuing the book
by the split ratio (shares too low, price too low, and the two errors
don't cancel — they multiply in the same direction).

Two pure primitives, no I/O, matching ``engine.restatement``'s style:

- ``detect_split`` compares a symbol's already-stored history against a
  freshly fetched series over their overlapping dates and returns the
  split ratio if — and only if — every drifted row shares one ratio within
  a tight tolerance AND the drifted rows form a single contiguous prefix
  in time, ending at one transition date with zero drift after it. Both
  conditions are required: the ratio-cluster check alone is a statistic
  over the *distribution* of ratios and, in principle, a tight cluster of
  scattered-in-time drift could satisfy it (found during review — see the
  ``test_detect_split_refuses_a_tight_but_temporally_scattered_cluster``
  regression test for the exact shape that would slip past cluster-tightness
  alone). The temporal check is the structural signature a real split
  actually has — every date before the split shows one ratio, every date
  after shows none — and it is what a scattered-drift symbol (real or
  synthetic) cannot mimic without the drift being contiguous, which by
  definition it is not. Getting this wrong in the false-positive direction
  is worse than the bug it fixes: a spurious detection would silently
  multiply a real position by a bogus ratio.

- ``apply_split`` scales a held position's ``shares``/``avg_cost`` by the
  detected ratio, preserving cost basis (``shares × avg_cost``) exactly.

Thresholds (see the docstring of ``detect_split`` for the numbers and the
real 11-symbol dataset they were measured against — commit range
``4b6b8556^..4b6b8556`` plus live ``ALV.DE``/``BMW.DE`` fetches).
"""

from __future__ import annotations

import math
import statistics

import pandas as pd

# A row counts as "drifted" once its ratio departs from 1.0 by more than
# this. Real unchanged rows in this store are bit-identical (measured 0.0%
# deviation on CRWD/TIT.MI's post-split dates when diffing 4b6b8556^ against
# 4b6b8556) — this is a generous floor to absorb ordinary float noise, set
# far below the smallest real split's deviation from 1.0 (HON, 4.65%).
_DRIFT_TOLERANCE = 0.003

# A split candidate needs at least this many drifted rows before its ratios
# are even clustered. Real splits carry hundreds (CRWD 552, DD 546, TIT.MI
# 2570); the store's single-row "Day-1" errors (Class B in the sweep
# measurement) are exactly 1 row, so this alone rules those out without
# needing to look at the ratio at all.
_MIN_DRIFTED_ROWS = 10

# At least this many overlapping dates must exist between `stored` and
# `fetched` before concluding anything — "a handful", per the design brief.
_MIN_OVERLAP_ROWS = 10

# Max relative spread — (max - min) / median — allowed within the drifted
# ratio cluster for it to count as "a single constant ratio". Measured
# against the 11 real splits swept in 4b6b8556: the tightest clusters are
# exact (0.0% spread — CRWD, DD, HON, SPGI, KLAC, CVNA, FDX); the loosest is
# WLN.PA at 2.23% (rounding noise on a very low post-split price, ~EUR
# 0.50). Measured against real ordinary drift by fetching live data for
# ALV.DE (4.20% spread across 24 drifted rows) and BMW.DE (6.08% across 32):
# both comfortably clear this bar. 3% leaves ~0.8 percentage points of
# margin below the tightest real drift and ~0.8 above the loosest real
# split — WLN.PA is the closest real split ever measured to this boundary.
_CLUSTER_TOLERANCE = 0.03

# The cluster's median ratio must differ from 1.0 by at least this much to
# be worth calling a split, rather than some benign near-1 systematic
# shift. HON (0.9535, 4.65% from 1.0) is the closest real split to this
# floor — ~1.65 percentage points of margin. A threshold tuned around a
# "round" ratio like 2:1 or 4:1 would miss both HON (0.9535) and SPGI
# (1.057); this floor is set by the closest real case, not a round number.
_MATERIALITY_FLOOR = 0.03


def _date_key(ts: object) -> str:
    return ts.date().isoformat() if hasattr(ts, "date") else str(ts)


def detect_split(stored: list[dict], fetched: pd.DataFrame) -> float | None:
    """Return the split ratio if `stored` and `fetched` disagree by one constant factor.

    Compares the ``close`` field of `stored` (rows as read from a
    ``{SYMBOL}.jsonl`` store file — the same shape ``engine.market_data``
    and ``engine.ohlcv_ingest`` use) against the ``Close`` column of
    `fetched` (a yfinance-shaped DataFrame, ``auto_adjust=False``) over
    their overlapping dates. Deliberately reads raw ``close``, not
    ``adj_close``: Yahoo restates raw ``close`` itself for a split (which is
    exactly the corruption this function detects) but re-bases
    ``adj_close`` after every dividend too, which would otherwise look
    identical to a split under a naive comparison — see the pre-flight
    finding in this plan's ledger.

    A split's signature, established empirically against the 11 tickers
    corrected in commit 4b6b8556 (CRWD, DD, TIT.MI, WLN.PA, and 7 others):
    every date on or before the split shows the SAME ratio
    (``stored_close / fetched_close``), and every date after it is
    unchanged (ratio 1.0) — a single contiguous drifted PREFIX in time,
    with a clean transition and zero drift after it. Ordinary drift (e.g.
    real ``SIE.DE``/``ALV.DE``/``BMW.DE``) looks nothing like this: a
    handful of rows scattered THROUGHOUT the history, interleaved with
    unchanged rows on both sides, at ratios spread wider than any real
    split's cluster (measured 4.2-6.1% relative spread vs. the tightest
    real split's 2.23%). Two independent checks enforce this: the ratio
    cluster must be tight (``_CLUSTER_TOLERANCE``) AND the drifted rows
    must be temporally contiguous (no drifted row after the first
    undrifted one, in date order). Either check alone is a statistic that
    a sufficiently unlucky (or adversarial) scattered-drift shape could in
    principle satisfy; both together require the actual split signature,
    not just its statistical shadow. Returns ``None`` on anything that
    doesn't match, including genuine ambiguity (e.g. two competing ratio
    clusters) — a missed detection leaves the pre-existing valuation bug in
    place; a false one would silently multiply a real position.

    Parameters
    ----------
    stored:
        Rows as read from the committed store, each at least
        ``{"date": "YYYY-MM-DD", "close": float}``.
    fetched:
        A DataFrame indexed by date-like values with a ``Close`` column
        (yfinance's raw, non-adjusted shape).

    Returns
    -------
    float | None
        The detected ratio (``stored_close / fetched_close``, e.g. ``4.0``
        for a 4-for-1 forward split, ``0.025`` for WLN.PA's 40-for-1
        reverse split), or ``None`` if no split is detected.
    """
    stored_close: dict[str, float] = {
        r["date"]: r["close"] for r in stored if r.get("close") is not None
    }

    dated_ratios: list[tuple[str, float]] = []
    for ts, row in fetched.iterrows():
        d = _date_key(ts)
        old = stored_close.get(d)
        if old is None or old == 0:
            continue
        new = row["Close"]
        if new is None:
            continue
        try:
            new = float(new)
        except (TypeError, ValueError):
            continue
        if new == 0 or math.isnan(new):
            continue
        dated_ratios.append((d, old / new))

    dated_ratios.sort(key=lambda pair: pair[0])  # chronological — required below

    if len(dated_ratios) < _MIN_OVERLAP_ROWS:
        return None

    drifted = [r for _, r in dated_ratios if abs(r - 1.0) > _DRIFT_TOLERANCE]
    if len(drifted) < _MIN_DRIFTED_ROWS:
        return None

    median_ratio = statistics.median(drifted)
    if median_ratio <= 0 or abs(median_ratio - 1.0) < _MATERIALITY_FLOOR:
        return None

    spread = (max(drifted) - min(drifted)) / median_ratio
    if spread > _CLUSTER_TOLERANCE:
        return None  # scattered drift, not a single-ratio split — refuse

    # Temporal contiguity: a real split's drifted rows form a single
    # PREFIX in chronological order — every date up to the transition is
    # drifted, every date after it is not. Once the first undrifted date is
    # seen (in date order), no later date may be drifted. This is what a
    # ratio cluster tight enough to clear _CLUSTER_TOLERANCE cannot, on its
    # own, guarantee — a handful of scattered-in-time drifted rows that
    # happen to sit close in value would still pass the cluster check but
    # fail this one, because real scattered drift is interleaved with
    # unchanged rows on both sides, not confined to one clean run.
    is_drifted = [abs(r - 1.0) > _DRIFT_TOLERANCE for _, r in dated_ratios]
    first_undrifted = next((i for i, d in enumerate(is_drifted) if not d), None)
    if first_undrifted is not None and any(is_drifted[first_undrifted:]):
        return None  # a drifted row reappears after an undrifted one — not a split

    return median_ratio


def apply_split(positions: list[dict], ticker: str, ratio: float) -> list[dict]:
    """Scale a held position's shares/avg_cost for a detected stock split.

    Multiplies ``shares`` by ``ratio`` and divides ``avg_cost`` by it, so
    the cost basis (``shares × avg_cost``) is unchanged. Positions for
    every other ticker pass through untouched. Pure: returns a new list,
    never mutates `positions` or its dicts in place.

    Parameters
    ----------
    positions:
        Position dicts as stored in ``portfolio.json`` (at least
        ``ticker``, ``shares``, ``avg_cost`` — any other keys, e.g.
        ``date_opened``/``grid_level``, are preserved unchanged).
    ticker:
        The split-affected ticker.
    ratio:
        ``stored_close / fetched_close`` for the overlapping pre-split
        rows, as returned by ``detect_split`` — e.g. ``4.0`` for a 4-for-1
        forward split, ``0.025`` for a 40-for-1 reverse split.

    Returns
    -------
    list[dict]
        A new list with the matching ticker's position adjusted.

    Raises
    ------
    ValueError
        If ``ratio`` is not strictly positive.
    """
    if ratio <= 0:
        raise ValueError(f"split ratio must be positive, got {ratio}")

    adjusted: list[dict] = []
    for position in positions:
        if position["ticker"] != ticker:
            adjusted.append(position)
            continue
        new_position = dict(position)
        new_position["shares"] = position["shares"] * ratio
        new_position["avg_cost"] = position["avg_cost"] / ratio
        adjusted.append(new_position)
    return adjusted
