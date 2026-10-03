#!/usr/bin/env python3
"""Read-only audit: which symbols drive the market-data incident tail?

Stage 6.1 of the market-data structural plan. The question it answers is the
one a curated universe turns on: if the fetch universe were cut to the N most
liquid symbols (plus everything a book holds, has ever ordered, or is measured
against), how many of the incidents since ``--since`` would have been avoided?

Per symbol, since ``--since`` (default 2026-08-01):

- ``gaps``      weekdays the symbol lacks that a majority of its exchange
                bucket holds (`engine.store_gaps.scan_store`, the same scan the
                nightly run uses). Bucket-wide absences are NOT counted: a
                holiday and a vendor hole look identical to the store.
- ``accepted``  entries in `data/market/store_gaps.json` on or after the
                window that a human accepted; ``open`` are the unaccepted ones.
- ``quar``      distinct quarantined (symbol, date) pairs in `data/market/quarantine/`
                dated in the window (repeat fetch attempts count once), minus days
                already counted as a gap or a ledger entry.
- ``acts``      corporate actions in `data/market/corporate_actions.jsonl`
                effective in the window.
- ``late``      stored rows dated in the window that first reached the repo
                two or more business days after their date (calendar days for
                crypto). Read from git history, so it measures arrival latency
                INCLUDING later repair commits; a row added by the commit that
                created its file (a first backfill) is not counted.
- ``stale``     the symbol's newest row is older than its bucket's newest.
                Scored only for a symbol still fetched (in the universe) or
                held; one that left the universe is stale by construction,
                even when it stays in the pool through ``ordered``.
- ``value_eur`` median daily traded value in EUR over the last ``--value-days``
                rows: volume x close for equities; for crypto the vendor's volume
                is already in the quote currency, so it is taken as is (x close
                would weight a coin by its price). Futures volume is in contracts
                and the store holds no contract multiplier, so futures cannot be
                valued. Instruments that carry no usable volume (FX pairs,
                indices, futures) cannot be ranked; they are always kept and
                flagged. Currencies the store has no pair for (SEK,
                NOK, DKK, PLN) use fixed approximate rates, for ranking only.

Nothing is written, fetched or committed. Exit 0 on a printed table; 2 when the
inputs cannot be read (an empty store is unknown, never "no incidents").
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from engine import fx  # noqa: E402
from engine.config import get_config  # noqa: E402
from engine.fees import classify_ticker  # noqa: E402
from engine.quotes import ticker_currency  # noqa: E402
from engine.store_gaps import is_accepted, parse_ledger, scan_store  # noqa: E402
from scripts import fetch_ohlcv  # noqa: E402
from scripts.fetch_ohlcv import (  # noqa: E402
    _all_symbols,
    _collect_holdings,
    _crypto_symbols,
    get_crypto_eur_tickers,
    get_crypto_tickers,
    hole_bucket,
)

DEFAULT_SINCE = "2026-08-01"
DEFAULT_CUTS = (150, 300, 500)
DEFAULT_VALUE_DAYS = 60
#: A row is late when it first arrived this many business days after its date.
LATE_AFTER_DAYS = 2
#: Order channels whose tickers count as "ever ordered".
_ORDER_CHANNELS = ("outbox", "pending", "manager-outbox", "manager-pending")

EXIT_UNKNOWN = 2

EVENT_METRICS = ("gaps", "accepted", "open", "quar", "acts", "stale")
#: A symbol with this many late rows in the window is chronically late.
CHRONIC_LATE_ROWS = 20


@dataclass
class SymbolRow:
    symbol: str
    bucket: str
    held: bool = False
    ordered: bool = False
    benchmark: bool = False
    in_universe: bool = False
    value_eur: float | None = None  # None: cannot be ranked (no volume, or no FX rate)
    value_stale: bool = False  # value_eur comes from rows older than the window
    no_fx: bool = False  # value_eur is None because no rate to EUR exists
    gaps: int = 0
    accepted: int = 0
    open: int = 0
    quar: int = 0
    acts: int = 0
    late: int = 0
    stale: int = 0
    rows_in_window: int = 0
    always: list[str] = field(default_factory=list)

    @property
    def events(self) -> int:
        return sum(getattr(self, m) for m in EVENT_METRICS)


def _read_store(ohlcv_dir: Path) -> dict[str, list[tuple[str, float | None, float | None]]]:
    """symbol -> [(date, close, volume)], tolerant of an unparseable line."""
    store: dict[str, list[tuple[str, float | None, float | None]]] = {}
    for path in sorted(ohlcv_dir.glob("*.jsonl")):
        rows = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                rows.append((r["date"], r.get("close"), r.get("volume")))
            except (ValueError, KeyError):
                continue
        store[path.stem] = rows
    return store


def _ordered_tickers(orders_dir: Path) -> set[str]:
    """Tickers ever ordered. Outbox channels hold JSONL files; the two pending
    channels hold one pretty-printed `ord_*.json` file per order."""
    seen: set[str] = set()
    for channel in _ORDER_CHANNELS:
        for path in sorted((orders_dir / channel).glob("*.jsonl")):
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    t = json.loads(line).get("ticker")
                except ValueError:
                    continue
                if t:
                    seen.add(t)
        for path in sorted((orders_dir / channel).glob("*.json")):
            try:
                t = json.loads(path.read_text()).get("ticker")
            except (ValueError, AttributeError):
                continue
            if t:
                seen.add(t)
    return seen


def _resolved_universe() -> tuple[set[str], frozenset[str]]:
    """(fetch universe, crypto set), refusing a partial one.

    The fetch helpers print a warning and carry on when a resolver raises; the
    populator checks `_resolver_failures` before acting. A partial universe here
    would reclassify whole indices as retired and drop crypto pairs into the US
    bucket, with a normal-looking report, so it is unknown (exit 2) instead.
    """
    universe = set(_all_symbols())
    failed = list(fetch_ohlcv._resolver_failures or [])
    if fetch_ohlcv._resolver_failures is None:
        failed.append("(universe resolvers did not run)")
    crypto = frozenset(_crypto_symbols())
    for resolver in (get_crypto_tickers, get_crypto_eur_tickers):
        try:
            resolver()
        except Exception:
            failed.append(resolver.__name__)
    if failed:
        raise RuntimeError(
            f"universe resolution failed for {', '.join(failed)}: unknown, not clean"
        )
    return universe, crypto


def _benchmark_tickers() -> set[str]:
    cfg = get_config()
    out = {cfg.global_reference.ticker}
    for agent in cfg.roster.values():
        if agent.benchmark:
            out.add(agent.benchmark.ticker)
    return out


class NoFxRate(Exception):
    """A symbol has traded value in a currency with no rate to EUR."""

    def __init__(self, currency: str):
        super().__init__(currency)
        self.currency = currency


#: EUR per one unit, used ONLY to rank symbols whose currency has no pair in the
#: store (it holds 10 `=X` pairs, none for these). Approximate on purpose: a
#: ranking by traded value tolerates a ~10% rate error, and nothing here is ever
#: written to the store or used to value a position. DKK is pegged to the euro.
RANKING_ONLY_EUR_RATES: dict[str, float] = {
    "SEK": 0.090,
    "NOK": 0.085,
    "DKK": 0.134,
    "PLN": 0.235,
}


def _is_future(symbol: str) -> bool:
    return symbol.endswith("=F")


def _median_value_eur(
    symbol: str,
    rows: list[tuple[str, float | None, float | None]],
    days: int,
    approx_used: set[str] | None = None,
    crypto: frozenset[str] = frozenset(),
) -> float | None:
    """Median daily traded value in EUR; None when there is no volume.

    Raises `NoFxRate` rather than return local units: a wrong currency still
    ranks, so that failure has no symptom (SEK/NOK ~11x too high). A currency the
    store has no rate for falls back to `RANKING_ONLY_EUR_RATES` and is recorded
    in ``approx_used``; one in neither raises. An unresolved currency (None) is
    unrankable too, never assumed to be EUR.

    Crypto volume is quoted in the pair's quote currency already, so it is not
    multiplied by the close. Futures volume counts contracts of an unknown size:
    None (unrankable) rather than a number that ranks by price.
    """
    if _is_future(symbol):
        return None
    is_crypto = symbol in crypto or classify_ticker(symbol) == "crypto"
    recent = sorted(rows)[-days:]
    if is_crypto:
        values = [v for _, _, v in recent if v and v > 0]
    else:
        values = [c * v for _, c, v in recent if c and v and v > 0]
    if not values:
        return None
    local = statistics.median(values)
    ccy = ticker_currency(symbol)
    if ccy is None:
        raise NoFxRate("UNRESOLVED")
    if ccy == "EUR":
        return local
    converted = fx.to_eur(local, ccy)
    if converted is None:
        rate = RANKING_ONLY_EUR_RATES.get(ccy)
        if rate is None:
            raise NoFxRate(ccy)
        if approx_used is not None:
            approx_used.add(ccy)
        return local * rate
    return converted


def _heaviest(rows: list[SymbolRow], n: int) -> list[SymbolRow]:
    """The ``n`` rows with the most events, ties by symbol."""
    return sorted(rows, key=lambda r: (-r.events, r.symbol))[:n]


def _value_asof(rows: list[tuple[str, float | None, float | None]], days: int) -> str:
    """Newest date among the rows `_median_value_eur` ranks on ('' when none)."""
    recent = sorted(rows)[-days:]
    return recent[-1][0] if recent else ""


def _bucketer(crypto: frozenset[str]):
    """`hole_bucket` bound to the crypto set, exactly as fetch_ohlcv calls it.

    Without the set, pairs the fee allowlist lacks (BNB-USD, HBAR-USD, ...) fall
    into the US bucket and their weekend rows make every US equity look stale.
    """
    return lambda symbol: hole_bucket(symbol, crypto)


def _late_rows(
    since: str,
    ohlcv_rel: str,
    repo: Path = _PROJECT_ROOT,
    crypto: frozenset[str] = frozenset(),
) -> dict[str, int]:
    """symbol -> rows dated >= since that first arrived >= LATE_AFTER_DAYS late.

    A row rewritten in place (``-`` and ``+`` of the same date in one file diff)
    is never an arrival; only a ``+`` with no matching ``-`` counts.
    """
    import numpy as np

    cmd = [
        "git", "-C", str(repo), "log", "--reverse", f"--since={since}",
        "--format=COMMIT_MARK %H %ct", "-p", "-U0", "--no-renames", "--", ohlcv_rel,
    ]
    first_seen: dict[tuple[str, str], str] = {}
    commit_day = ""
    path = ""
    new_file = False
    removed: set[str] = set()  # dates this file's diff removes: a `+` of one is a rewrite
    with subprocess.Popen(
        cmd, stdout=subprocess.PIPE, text=True, errors="replace"
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.startswith("COMMIT_MARK "):
                ts = int(line.split()[2])
                commit_day = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
            elif line.startswith("diff --git"):
                new_file = False
                removed = set()
            elif line.startswith("new file mode"):
                new_file = True
            elif line.startswith("+++ b/"):
                path = Path(line[6:].strip()).stem
            elif line.startswith('-{"date"'):
                try:
                    removed.add(json.loads(line[1:])["date"])
                except (ValueError, KeyError):
                    continue
            elif line.startswith('+{"date"') and not new_file:
                try:
                    d = json.loads(line[1:])["date"]
                except (ValueError, KeyError):
                    continue
                # A `+` row whose date the same diff removes is a rewrite
                # (resweep, restatement, revision), not an arrival: its first
                # arrival was an earlier commit, a skipped new-file backfill or
                # a commit before the window.
                if d >= since and d not in removed:
                    first_seen.setdefault((path, d), commit_day)
        if proc.wait() != 0:
            raise RuntimeError("git log failed")

    bucket_of = _bucketer(crypto)
    late: dict[str, int] = defaultdict(int)
    for (symbol, d), arrived in first_seen.items():
        if bucket_of(symbol) == "crypto":
            lag = (date.fromisoformat(arrived) - date.fromisoformat(d)).days
        else:
            lag = int(np.busday_count(d, arrived))
        if lag >= LATE_AFTER_DAYS:
            late[symbol] += 1
    return late


def _unledgered_gaps(member_gaps: frozenset[str], ledger_entries: dict) -> int:
    """Gap dates not already counted as an accepted/open ledger entry.

    scan_store does not read the ledger, so a ledger date absent from the store
    is in both; counting both would make one missing day two events.
    """
    return len(set(member_gaps) - set(ledger_entries))


def _quarantine_counts(quarantine_dir: Path, since: str) -> dict[str, int]:
    """symbol -> distinct quarantined dates >= since (not repeat fetch attempts)."""
    return {s: len(ds) for s, ds in _quarantine_dates(quarantine_dir, since).items()}


def _unledgered_quarantine(
    quarantined: set[str], member_gaps: frozenset[str], ledger_entries: dict
) -> int:
    """Quarantined dates not already counted as a gap or a ledger entry.

    A quarantined row is never stored, so its day is also a scan gap or a ledger
    entry (BYND 2026-08-13 is accepted AND quarantined): one missing day is one
    event, not two.
    """
    return len(set(quarantined) - set(member_gaps) - set(ledger_entries))


def _quarantine_dates(quarantine_dir: Path, since: str) -> dict[str, set[str]]:
    """symbol -> distinct quarantined dates >= since."""
    seen: dict[str, set[str]] = defaultdict(set)
    for path in quarantine_dir.glob("*.jsonl"):
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("date", "") >= since:
                seen[r.get("symbol", path.stem)].add(r["date"])
    return dict(seen)


def _bucket_newest(
    dates: dict[str, frozenset[str]], bucket_of
) -> dict[str, str]:
    """bucket -> newest date any member holds."""
    newest: dict[str, str] = {}
    for s, ds in dates.items():
        b = bucket_of(s)
        newest[b] = max(newest.get(b, ""), max(ds))
    return newest


def _counts_as_stale(r: SymbolRow, newest: str, bucket_newest: str) -> bool:
    """A frozen file is a vendor incident only while something still fetches it."""
    return (r.in_universe or r.held) and newest < bucket_newest


def build_rows(
    since: str, value_days: int, allow_missing_fx: bool = False
) -> tuple[list[SymbolRow], list[SymbolRow], dict[str, object]]:
    cfg = get_config()
    store = _read_store(cfg.ohlcv_dir)
    if not store:
        raise RuntimeError("the OHLCV store is empty: unknown, not clean")

    universe, crypto = _resolved_universe()
    held = _collect_holdings()
    ordered = _ordered_tickers(cfg.orders_dir)
    bench = _benchmark_tickers()

    dates = {s: frozenset(d for d, _, _ in rows) for s, rows in store.items() if rows}
    # `end`: newest date held by at least half the store, so a lagging bucket's
    # missing last day is not read as a store-wide gap.
    counts: dict[str, int] = defaultdict(int)
    for ds in dates.values():
        for d in ds:
            counts[d] += 1
    end = max((d for d, n in counts.items() if n >= len(dates) / 2), default=None)
    if end is None or end < since:
        raise RuntimeError("no date in the window is held by half the store")

    bucket_of = _bucketer(crypto)
    scan = scan_store(dates, bucket_of=bucket_of, scope=dates, start=since, end=end)

    ledger = parse_ledger((cfg.ohlcv_dir.parent / "store_gaps.json").read_text())
    quarantine = _quarantine_dates(cfg.ohlcv_dir.parent / "quarantine", since)
    actions: dict[str, int] = defaultdict(int)
    actions_path = cfg.ohlcv_dir.parent / "corporate_actions.jsonl"
    for line in actions_path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            if r["effective"] >= since:
                actions[r["symbol"]] += 1

    ohlcv_rel = str(cfg.ohlcv_dir.relative_to(_PROJECT_ROOT))
    late = _late_rows(since, ohlcv_rel, crypto=crypto)
    bucket_newest = _bucket_newest(dates, bucket_of)

    pool = sorted(set(store) & (universe | held | ordered | bench))

    missing_fx: dict[str, list[str]] = {}
    approx_used: set[str] = set()

    def make_row(s: str) -> SymbolRow:
        r = SymbolRow(
            symbol=s,
            bucket=bucket_of(s),
            held=s in held,
            ordered=s in ordered,
            benchmark=s in bench,
            in_universe=s in universe,
        )
        try:
            r.value_eur = _median_value_eur(s, store[s], value_days, approx_used, crypto)
            r.value_stale = r.value_eur is not None and _value_asof(store[s], value_days) < since
        except NoFxRate as exc:
            r.no_fx = True
            missing_fx.setdefault(exc.currency, []).append(s)
        member_gaps = scan.member_gaps.get(s, frozenset())
        r.gaps = _unledgered_gaps(member_gaps, ledger.get(s, {}))
        for d, entry in ledger.get(s, {}).items():
            if d >= since:
                if is_accepted(entry):
                    r.accepted += 1
                else:
                    r.open += 1
        r.quar = _unledgered_quarantine(quarantine.get(s, set()), member_gaps, ledger.get(s, {}))
        r.acts = actions.get(s, 0)
        r.late = late.get(s, 0)
        newest = max(dates[s]) if s in dates else ""
        r.stale = int(_counts_as_stale(r, newest, bucket_newest.get(r.bucket, "")))
        r.rows_in_window = sum(1 for d in dates.get(s, ()) if d >= since)
        return r

    rows = [make_row(s) for s in pool]
    # Symbols that left the universe still carry the window's worst incidents
    # (AVB): a cut cannot avoid them now, but the record must show them.
    retired = [
        r
        for r in (make_row(s) for s in sorted(set(store) - set(pool)))
        if r.events
    ]

    missing_pool = {
        c: [x for x in syms if x in set(pool)] for c, syms in missing_fx.items()
    }
    missing_pool = {c: v for c, v in missing_pool.items() if v}
    if missing_pool and not allow_missing_fx:
        detail = ", ".join(f"{c}({len(v)})" for c, v in sorted(missing_pool.items()))
        raise RuntimeError(
            f"no EUR rate for {detail}: ranking them in local units would mix "
            f"currencies; pass --allow-missing-fx to mark them unrankable instead"
        )

    meta = {
        "approx_fx": sorted(approx_used),
        "missing_fx": {c: len(v) for c, v in sorted(missing_pool.items())},
        "since": since,
        "end": end,
        "store_files": len(store),
        "universe_symbols": len(universe),
        "universe_in_store": len(universe & set(store)),
        "held": len(held),
        "ordered": len(ordered),
        "benchmarks": len(bench),
        "pool": len(rows),
        "outside_pool_files": len(set(store) - set(pool)),
        "retired_with_events": len(retired),
        "bucket_wide_candidates": len(scan.bucket_candidates),
    }
    return rows, retired, meta


def choose_cut(rows: list[SymbolRow], n: int) -> set[str]:
    """Top ``n`` by median traded value, plus every always-kept symbol.

    Symbols with no volume series cannot be ranked and are always kept (FX
    pairs and indices: a handful, and a book that trades them has no other
    liquidity proxy).
    """
    ranked = sorted(
        (r for r in rows if r.value_eur is not None),
        key=lambda r: (-(r.value_eur or 0.0), r.symbol),
    )
    keep = {r.symbol for r in ranked[:n]}
    keep |= {r.symbol for r in rows if r.held or r.ordered or r.benchmark}
    keep |= {r.symbol for r in rows if r.value_eur is None}
    return keep


def _floor_extra(
    rows: list[SymbolRow], forced: set[str], rank_of: dict[str, int], n: int
) -> list[SymbolRow]:
    """Floor symbols that are ranked, and ranked below ``n``; unrankable ones have no rank."""
    return [r for r in rows if r.symbol in forced and r.symbol in rank_of and rank_of[r.symbol] > n]


def format_report(
    rows: list[SymbolRow], retired: list[SymbolRow], meta: dict[str, object], cuts: list[int], top: int | None
) -> str:
    out: list[str] = []
    w = out.append
    w(f"Universe reliability audit, window {meta['since']} .. {meta['end']}")
    w(f"  store files {meta['store_files']}, fetch universe {meta['universe_symbols']} "
      f"({meta['universe_in_store']} in store), held {meta['held']} distinct tickers, "
      f"ever ordered {meta['ordered']}, benchmark tickers {meta['benchmarks']}")
    w(f"  audited pool {meta['pool']} symbols; {meta['outside_pool_files']} retired store "
      f"files sit outside it")
    w(f"  {meta['retired_with_events']} of those carry events in the window: "
      f"{', '.join(f'{r.symbol}({r.events})' for r in _heaviest(retired, 12)) or 'none'}; "
      f"{sum(r.events for r in retired)} events in total, not in the cut table "
      f"(no cut can avoid what the universe already shed)")
    w(f"  bucket-wide absences (holiday-ambiguous, not counted as gaps): "
      f"{meta['bucket_wide_candidates']} (bucket, date) pairs")
    unrank = sum(1 for r in rows if r.value_eur is None and not r.no_fx)
    w(f"  {unrank} symbols carry no comparable volume series (FX, indices, futures) and cannot be ranked; always kept")
    if meta.get("approx_fx"):
        w(f"  NOTE: {', '.join(meta['approx_fx'])} have no rate in the store; ranked with fixed "
          f"approximate rates (RANKING_ONLY_EUR_RATES), good enough to order, not to value")
    stale_val = [r.symbol for r in rows if r.value_stale]
    if stale_val:
        w(f"  NOTE: {len(stale_val)} symbols are ranked on rows older than the window "
          f"(store stopped before {meta['since']}); marked * in the table")
    nofx = sum(1 for r in rows if r.no_fx)
    if nofx:
        w(f"  WARNING: {nofx} symbols have no EUR rate ({meta.get('missing_fx')}); unrankable, "
          f"always kept, so every cut below OVERSTATES what it keeps and understates avoided")
    w("")

    tot_ev = sum(r.events for r in rows)
    tot_late = sum(r.late for r in rows)

    def pct(dropped: int, total: int) -> str:
        return f"{100 * dropped / total:.0f}%" if total else "-"

    head = (f"{'cut':<24}{'symbols':>8}{'gaps':>6}{'accpt':>6}{'open':>6}{'quar':>6}"
            f"{'acts':>6}{'stale':>6}{'events':>8}{'avoided':>9}{'late rows':>11}"
            f"{'avoided':>9}{'chronic':>9}")
    w(head)
    w("-" * len(head))

    def line(label: str, keep: set[str] | None) -> str:
        sel = [r for r in rows if keep is None or r.symbol in keep]
        s = {m: sum(getattr(r, m) for r in sel) for m in EVENT_METRICS}
        ev = sum(s.values())
        late = sum(r.late for r in sel)
        chronic = sum(1 for r in sel if r.late >= CHRONIC_LATE_ROWS)
        return (f"{label:<24}{len(sel):>8}{s['gaps']:>6}{s['accepted']:>6}{s['open']:>6}"
                f"{s['quar']:>6}{s['acts']:>6}{s['stale']:>6}{ev:>8}"
                f"{pct(tot_ev - ev, tot_ev):>9}{late:>11}{pct(tot_late - late, tot_late):>9}"
                f"{chronic:>9}")

    w(line("full pool (today)", None))
    forced = {r.symbol for r in rows if r.held or r.ordered or r.benchmark or r.value_eur is None}
    w(line("always-kept floor only", forced))
    for n in cuts:
        w(line(f"top {n} + always-kept", choose_cut(rows, n)))
    w("")
    w("events = gaps + accepted + open + quar + acts + stale; avoided = share of the pool's")
    w("  events (or late rows) that sit on symbols the cut drops.")
    w(f"late rows = rows first committed >= {LATE_AFTER_DAYS} business days after their date "
      f"(repair commits included).")
    w(f"chronic = symbols in the cut with >= {CHRONIC_LATE_ROWS} late rows.")
    w("always-kept = held now, ever ordered, benchmark tickers, and unrankable (no volume).")
    w("")

    ranked = sorted((r for r in rows if r.value_eur is not None), key=lambda r: -(r.value_eur or 0))
    rank_of = {r.symbol: i + 1 for i, r in enumerate(ranked)}
    w("What each cut drops, and what the floor costs:")
    for n in cuts:
        keep = choose_cut(rows, n)
        dropped = [r for r in rows if r.symbol not in keep and r.events]
        by_bucket: dict[str, int] = defaultdict(int)
        for r in dropped:
            by_bucket[r.bucket or "US"] += r.events
        spread = ", ".join(f"{b}:{c}" for b, c in sorted(by_bucket.items(), key=lambda kv: -kv[1])[:6])
        extra = _floor_extra(rows, forced, rank_of, n)
        w(f"  top {n}: drops {len(dropped)} symbols carrying {sum(r.events for r in dropped)} events "
          f"[{spread}]; the floor adds {len(extra)} symbols ranked below {n}, "
          f"{sum(r.events for r in extra)} events, {sum(r.late for r in extra)} late rows")
    w("")

    shown = sorted(rows, key=lambda r: (-r.events, -r.late, r.symbol))
    if top is not None:
        shown = shown[:top]
    w(f"{'symbol':<12}{'bkt':<7}{'rank':>6}{'value_eur':>15}  {'HOB':<5}{'gaps':>5}{'accpt':>6}"
      f"{'open':>5}{'quar':>5}{'acts':>5}{'stale':>6}{'late':>6}")
    for r in shown:
        flags = ("H" if r.held else "-") + ("O" if r.ordered else "-") + ("B" if r.benchmark else "-")
        rk = rank_of.get(r.symbol)
        val = f"{r.value_eur:,.0f}" if r.value_eur is not None else "n/a"
        if r.value_stale:
            val += "*"
        w(f"{r.symbol:<12}{(r.bucket or 'US'):<7}{(rk if rk else '-'):>6}{val:>15}  {flags:<5}"
          f"{r.gaps:>5}{r.accepted:>6}{r.open:>5}{r.quar:>5}{r.acts:>5}{r.stale:>6}{r.late:>6}")
    w(f"({len(shown)} of {len(rows)} symbols shown, most events first; --top 0 lists all)")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--since", default=DEFAULT_SINCE)
    p.add_argument("--cuts", default=",".join(str(c) for c in DEFAULT_CUTS))
    p.add_argument("--value-days", type=int, default=DEFAULT_VALUE_DAYS)
    p.add_argument("--allow-missing-fx", action="store_true",
                   help="mark symbols with no EUR rate unrankable instead of exiting 2")
    p.add_argument("--top", type=int, default=40, help="symbols listed, most events first; 0 lists all")
    args = p.parse_args(argv)
    try:
        cuts = [int(c) for c in args.cuts.split(",") if c.strip()]
        rows, retired, meta = build_rows(args.since, args.value_days, args.allow_missing_fx)
    except Exception as exc:  # unknown, never "clean"
        print(f"audit could not run: {exc}", file=sys.stderr)
        return EXIT_UNKNOWN
    print(format_report(rows, retired, meta, cuts, args.top or None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
