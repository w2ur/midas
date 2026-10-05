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

import json
import math
from dataclasses import dataclass, fields
from datetime import date, timedelta
from pathlib import Path
from typing import Collection, Iterator, Mapping

import pandas as pd

from engine.config import BenchmarkSpec, get_config
from engine.disclosure import require_changelog_entry


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


def _load_price_frame(
    tickers: list[str], from_date: date, to_date: date
) -> pd.DataFrame:
    """Build a DataFrame of daily closes over the date range for the given tickers.

    Missing rows are forward-filled; tickers with no file at all are dropped.
    """
    series_by_ticker: dict[str, pd.Series] = {}
    for t in tickers:
        closes = _load_ohlcv(t)
        if not closes:
            continue
        s = pd.Series({pd.Timestamp(d): v for d, v in closes.items()}).sort_index()
        series_by_ticker[t] = s
    if not series_by_ticker:
        return pd.DataFrame()
    df = pd.DataFrame(series_by_ticker)
    idx = pd.date_range(from_date, to_date, freq="D")
    return df.reindex(idx).ffill()


def compute_coin_flip(
    agent_id: str,
    tickers: list[str],
    currency: str,
    max_positions: int,
    from_date: date,
    to_date: date,
) -> list[dict]:
    """Random-trader-in-same-universe, €10k or $10k start, deterministic per agent.

    Builds the bt pipeline directly to avoid build_bt_strategy's StatTotalReturn
    + SelectN insertion, which would override the seeded picks with return-rank
    ordering. The seeded selector already caps picks at max_positions so no
    SelectN step is needed; LimitWeights stays as a safety valve for days when
    the available universe (after dropna) is smaller than max_positions, which
    would otherwise let WeighEqually allocate >1/max_positions to a single name.

    **This series is NOT invariant to the scale its prices are quoted in**, and
    that is worth knowing before any future rescaling of the store. `bt.Backtest`
    defaults to ``integer_positions=True``, so share counts are rounded down and
    the residue depends on absolute price: at 116 a €2,000 sleeve buys 17 shares,
    at 1.16 it buys 1,724, and the leftover cash differs. Normalising the store
    from pence to pounds on 2026-08-07 therefore moved this control by up to
    **3.79%** on a single day (`goldfinger`, 2026-07-31) even though no return
    changed — the series was restated onto the new basis rather than left with a
    seam. The passive benchmark next door has no such exposure: it is computed
    from price *ratios*, which cancel any constant factor.

    So a stock split, a new sub-unit ticker, or any other rescaling will shift
    this curve again. Fixing it permanently means ``integer_positions=False``,
    which was considered and declined: a control that cannot hold a whole share
    is less honest about what a random trader could actually have done.
    """
    import bt as _bt

    from engine.selectors.random_seeded import SelectRandomlySeeded, make_seed

    price_data = _load_price_frame(tickers, from_date, to_date)
    if price_data.empty:
        return []

    seed = make_seed(agent_id, from_date.isoformat())
    strategy_id = f"coinflip-{agent_id}"
    max_weight = 1.0 / max(max_positions, 1)

    pipeline = [
        _bt.algos.RunDaily(),
        SelectRandomlySeeded(n=max_positions, seed=seed),
        _bt.algos.WeighEqually(),
        _bt.algos.LimitWeights(max_weight),
        _bt.algos.Rebalance(),
    ]
    strategy = _bt.Strategy(strategy_id, pipeline)
    backtest = _bt.Backtest(strategy, price_data, initial_capital=_initial())
    bt_result = _bt.run(backtest)

    daily_values: pd.Series = bt_result.backtests[strategy_id].strategy.values
    snaps = [
        {"date": idx.date().isoformat(), "portfolioValue": float(val)}
        for idx, val in daily_values.items()
    ]
    return [
        {
            "date": s["date"],
            "portfolio_value": float(s["portfolioValue"]),
            "cash": 0.0,
            "positions_value": float(s["portfolioValue"]),
            "currency": currency,
        }
        for s in snaps
        if from_date.isoformat() <= s["date"] <= to_date.isoformat()
    ]


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
    path_recompute: int = 0

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
            + self.path_recompute
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
    (
        "path_recompute",
        "coin-flip point(s) differ from a recomputation of the seeded path",
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
    kind: str,
    sidecar: dict[str, dict],
    closes: Mapping[str, float] | None,
) -> str | None:
    """The class of a published row against its recomputation, or None if equal."""
    if kind == "coinflip":
        return None if published == computed else "path_recompute"
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
    kind: str = "benchmark",
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
    which landed later, and coin-flip path recomputes. A count dominated by
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
    - ``path_recompute`` — ``kind="coinflip"``: any coin-flip mismatch, until
      plan 1.6 replaces that merge with a stateful advance. Never a concern.

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
    kind:
        ``"benchmark"`` (a passive benchmark or the global reference: marks
        are classified) or ``"coinflip"`` (every mismatch is
        ``path_recompute``).
    closes:
        The store's ``{date: close}`` for the benchmark's ticker. Without it
        no recorded mark can be confirmed, so every mark-bearing mismatch is
        a concern.

    Returns
    -------
    MergeCounts
        Appended dates and the mismatches per class.
    """
    if kind not in ("benchmark", "coinflip"):
        raise ValueError(f"unknown baseline kind {kind!r}")
    existing: list[dict] = json.loads(path.read_text()) if path.exists() else []
    if not computed and existing:
        print(
            f"  [WARN] {path.name}: computed series is empty against "
            f"{len(existing)} published row(s) — likely a missing OHLCV "
            f"ticker file (within-range gaps are already forward-filled), "
            f"not a transient blip. Keeping the published file as-is."
        )
        return MergeCounts()
    sidecar = _load_marks_sidecar(path) if kind == "benchmark" and existing else {}
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
        verdict = _classify(
            published, row, kind=kind, sidecar=sidecar, closes=closes
        )
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

    A scope entry is either a bare kind (`"coinflip"` — every agent's coin
    flip) or a fully-qualified `"<agent>/<kind>"` (`"goldfinger/coinflip"`).

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
    differ, and for the mismatch classes).

    **What reaches the session's ``Concerns:`` path.** The session model
    turns printed ``[WARN]`` lines into commit trailers, so only a
    ``concern`` prints as one: each row as it is found, then one aggregate
    ``[WARN] baselines: N concern(s)`` line. Every expected class
    (``stale_mark``, ``rescaled``, ``unclassified``, ``path_recompute``)
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
        coin = compute_coin_flip(
            agent_id=agent_id,
            tickers=tickers,
            currency=spec.currency,
            max_positions=max_pos,
            from_date=from_date,
            to_date=to_date,
        )
        totals += merge_baseline_series(
            agent_dir / "coinflip.json",
            coin,
            restate=_series_restated(restate_series, agent_id, "coinflip"),
            kind="coinflip",
        )

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
