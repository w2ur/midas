#!/usr/bin/env python3
"""Read-only audit: the splice seams in the published coin-flip series.

Until plan 1.6 (2026-10-05) each session recomputed every coin flip from day one
with a ``bt`` backtest over that session's universe and price store, and the
append-only merge kept each published row exactly as its own session computed
it. So the published curve is a splice: each row is frozen from whichever path
its session had, and where two consecutive rows were written by sessions with
different inputs (a universe refresh, a store revision, a corporate-action
rescale) the published day-over-day return is not the return of any single
path. This script counts those seams. It writes nothing.

Method, per agent:

1. **Writers.** Walk the git history of ``data/baselines/<agent>/coinflip.json``
   up to ``--tip``; the writer of a row is the last commit that set its value.
2. **Boundaries.** Every pair of consecutive published dates ``(p, d)`` whose
   writers differ. A boundary at or after the agent's state migration (a writer
   whose tree holds ``data/baselines/<agent>/state/coinflip.json``) is not a
   pre-1.6 boundary and is skipped.
3. **The one-path return.** Recompute the series from day one to ``d`` with the
   *later* writer's inputs — its universe and ``max_positions`` (resolved by its
   own ``scripts/backfill_baselines.py`` in its own tree, ``<writer>^``) and its
   price store (``<writer>^:data/market/ohlcv``) — and the pre-1.6 ``bt``
   pipeline, kept self-contained below. The one-path return is ``v[d]/v[p]-1``,
   the published one ``pub[d]/pub[p]-1``.
4. **Control.** The recomputation must reproduce the writer's own row,
   ``v[d] == pub[d]`` (relative ``REPRODUCE_REL_TOL``). A boundary where it
   does not is reported as ``unreproduced`` and counted in no seam figure:
   the method cannot speak to it.
5. **Seam.** A reproduced boundary where ``|published - one-path| >
   SEAM_ABS_TOL``. The gap is published minus one-path, in percentage points.

**It audits the history before the coin flips were restated, by default**
(round-4 review, 2026-10-06). The 2026-10-06 ``[restate]`` commit replaced
every published coin flip with one continuous path, so the tree after it has
almost no boundary left to measure and the audit would exit 2 forever on a
correct tree. Without ``--at`` it measures the parent of the oldest
``[restate]`` commit that wrote a coin-flip state
(``data/baselines/<agent>/state/coinflip.json``): the published history the
METHODOLOGY entries quote. A history with no such commit is measured at
``HEAD``. ``--at <rev>`` measures any other commit (``--tip`` is an alias).

Exit codes: 0 the audit ran (seams are a disclosure, not a failure); 2 unknown
— no history, no boundary checked, a writer tree whose universe could not be
resolved, or more than ``MAX_UNREPRODUCED_SHARE`` of boundaries unreproduced.
Never "clean" on a run that could not measure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

#: A recomputation reproduces a published row within this relative tolerance.
#: Same code and same inputs reproduce bit for bit; the slack is for a pandas
#: or bt build differing in the last ulp.
REPRODUCE_REL_TOL = 1e-9

#: A published return differing from the one-path return by more than this
#: (absolute, as a fraction) is a seam. A cent on 10,000 is 1e-6; this sits
#: three orders below it so that float noise is never counted and any real
#: splice is.
SEAM_ABS_TOL = 1e-9

#: Seams at least this large are also counted separately (0.5 percentage point).
MATERIAL_GAP = 0.005

#: More boundaries than this share that the method cannot speak to
#: (unresolved or unreproduced) makes the audit unknown.
MAX_UNMEASURED_SHARE = 0.10

#: The tree paths a writer's universe resolution depends on.
EPOCH_PATHS = (
    "engine",
    "scripts",
    "data/universes",
    "roster.yaml",
)

_ROW = re.compile(
    rb'"date":\s*"(\d{4}-\d\d-\d\d)"[^\n]*?"close":\s*(null|[-+0-9.eE]+)'
    rb'(?:[^\n]*?"adj_close":\s*(null|[-+0-9.eE]+))?'
)


class Unknown(Exception):
    """The audit cannot measure what it was asked to (exit 2)."""


def _git(repo: Path, *args: str, binary: bool = False):
    out = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, check=True
    ).stdout
    return out if binary else out.decode()


class _Blobs:
    """``git cat-file --batch`` over one long-lived process."""

    def __init__(self, repo: Path) -> None:
        self._p = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=repo,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def get(self, spec: str) -> bytes | None:
        assert self._p.stdin and self._p.stdout
        self._p.stdin.write(spec.encode() + b"\n")
        self._p.stdin.flush()
        # "<sha> <type> <size>", or "<spec> missing" — and a spec can hold a
        # space (`AMBU B.CO`), so read the header from its end.
        header = self._p.stdout.readline().split()
        if not header or header[-1] == b"missing":
            return None
        size = int(header[-1])
        data = self._p.stdout.read(size)
        self._p.stdout.read(1)
        return data

    def close(self) -> None:
        if self._p.stdin:
            self._p.stdin.close()
        self._p.wait()
        if self._p.stdout:
            self._p.stdout.close()


def _closes(blob: bytes | None, since: str, adjusted: bool) -> dict[str, float]:
    """``{date: close}`` from a store file, dates on or after ``since``, on the
    basis the writer's own code read: raw ``close`` from 2026-08-07, and
    ``adj_close or close`` before (``adjusted``)."""
    if not blob:
        return {}
    out: dict[str, float] = {}
    for m in _ROW.finditer(blob):
        d = m.group(1).decode()
        if d < since:
            continue
        raw, adj = m.group(2), m.group(3)
        value = adj if adjusted and adj not in (None, b"null") and float(adj) else raw
        if value != b"null":
            out[d] = float(value)
    return out


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def series_writers(
    repo: Path, tip: str, agent: str
) -> tuple[dict[str, float], dict[str, str]]:
    """Published ``{date: value}`` at ``tip`` and ``{date: writer sha}``."""
    path = f"data/baselines/{agent}/coinflip.json"
    commits = _git(repo, "log", "--format=%H", "--reverse", tip, "--", path).split()
    current: dict[str, float] = {}
    writer: dict[str, str] = {}
    for c in commits:
        try:
            rows = json.loads(_git(repo, "show", f"{c}:{path}"))
        except (subprocess.CalledProcessError, ValueError):
            continue  # deleted or unreadable at this commit: nothing written
        values = {r["date"]: r["portfolio_value"] for r in rows}
        for d, v in values.items():
            if current.get(d) != v:
                writer[d] = c
        current = values
    published = {
        r["date"]: r["portfolio_value"]
        for r in json.loads(_git(repo, "show", f"{tip}:{path}"))
    }
    return published, {d: writer[d] for d in published if d in writer}


def has_state(repo: Path, sha: str, agent: str) -> bool:
    try:
        _git(repo, "cat-file", "-e", f"{sha}:data/baselines/{agent}/state/coinflip.json")
        return True
    except subprocess.CalledProcessError:
        return False


@dataclass(frozen=True)
class Boundary:
    agent: str
    prev: str
    date: str
    prev_writer: str
    writer: str


def boundaries(
    repo: Path, agent: str, published: dict[str, float], writer: dict[str, str]
) -> list[Boundary]:
    dates = sorted(d for d in published if d in writer)
    out = []
    state_cache: dict[str, bool] = {}

    def migrated(sha: str) -> bool:
        if sha not in state_cache:
            state_cache[sha] = has_state(repo, sha, agent)
        return state_cache[sha]

    for p, d in zip(dates, dates[1:]):
        if writer[p] == writer[d]:
            continue
        if migrated(writer[p]) or migrated(writer[d]):
            continue
        out.append(Boundary(agent, p, d, writer[p], writer[d]))
    return out


# ---------------------------------------------------------------------------
# A writer's inputs
# ---------------------------------------------------------------------------


def _epoch_key(repo: Path, rev: str) -> str:
    trees = []
    for p in EPOCH_PATHS:
        try:
            trees.append(_git(repo, "rev-parse", f"{rev}:{p}").strip())
        except subprocess.CalledProcessError:
            trees.append("-")
    return hashlib.sha1(" ".join(trees).encode()).hexdigest()[:12]


#: Run inside an extracted writer tree, with that tree's own code. Only the
#: config-driven resolvers (``_universes_by_agent``, from 2026-07) are
#: supported; an older tree is reported as unresolved, never guessed at.
_RESOLVE = (
    "import sys, json, os; sys.path.insert(0, os.getcwd());"
    "os.environ['MIDAS_DATA_DIR'] = os.getcwd();"
    "from scripts.backfill_baselines import _universes_by_agent as u, "
    "_max_positions_by_agent as m;"
    "from engine.config import get_config as g; c = g();"
    "print(json.dumps({'universes': u(), 'max_positions': m(),"
    " 'day_one': c.day_one.isoformat(), 'initial': c.initial_capital}))"
)


def resolve_inputs(repo: Path, rev: str, workdir: Path, cache: dict) -> dict:
    """Universes, max_positions, day one and initial capital as ``rev``'s own
    code resolved them, run in an extracted copy of ``rev``'s tree."""
    key = _epoch_key(repo, rev)
    if key in cache:
        return cache[key]
    target = workdir / key
    target.mkdir(parents=True, exist_ok=True)
    present = [p for p in EPOCH_PATHS if _exists(repo, rev, p)]
    archive = subprocess.run(
        ["git", "archive", rev, *present], cwd=repo, capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(target)], input=archive, check=True)
    r = subprocess.run(
        [sys.executable, "-c", _RESOLVE],
        cwd=target,
        capture_output=True,
        text=True,
        env={**os.environ, "MIDAS_DATA_DIR": str(target)},
    )
    if r.returncode != 0:
        cache[key] = {"unresolved": r.stderr.strip().splitlines()[-1][:200] if r.stderr.strip() else "failed"}
        return cache[key]
    inputs = json.loads(r.stdout.strip().splitlines()[-1])
    source = _git(repo, "show", f"{rev}:engine/baselines.py")
    inputs["adjusted"] = 'row.get("adj_close")' in source
    cache[key] = inputs
    return cache[key]


def _exists(repo: Path, rev: str, path: str) -> bool:
    try:
        _git(repo, "cat-file", "-e", f"{rev}:{path}")
        return True
    except subprocess.CalledProcessError:
        return False


# ---------------------------------------------------------------------------
# The pre-1.6 computation, self-contained
# ---------------------------------------------------------------------------


def pre16_coin_flip(
    agent: str,
    closes_by_ticker: dict[str, dict[str, float]],
    tickers: list[str],
    max_positions: int,
    day_one: str,
    to: str,
    initial: float,
) -> dict[str, float]:
    """``engine.baselines.compute_coin_flip`` as it stood before plan 1.6:
    ``RunDaily -> SelectRandomlySeeded(seed of (agent, day one)) ->
    WeighEqually -> LimitWeights(1/n) -> Rebalance`` over calendar days, closes
    forward-filled, tickers in the universe's own order, integer positions.
    """
    import random as _random

    import bt
    import pandas as pd

    from engine.selectors.seeding import make_seed

    class _Seeded(bt.Algo):
        def __init__(self, n: int, seed: int) -> None:
            super().__init__()
            self.n = n
            self._rng = _random.Random(seed)

        def __call__(self, target) -> bool:
            universe = target.universe.loc[target.now].dropna()
            candidates = list(universe.index)
            if not candidates:
                target.temp["selected"] = []
                return True
            target.temp["selected"] = self._rng.sample(
                candidates, k=min(self.n, len(candidates))
            )
            return True

    series = {}
    for t in tickers:
        closes = closes_by_ticker.get(t)
        if closes:
            series[t] = pd.Series(
                {pd.Timestamp(d): v for d, v in closes.items()}
            ).sort_index()
    if not series:
        return {}
    frame = pd.DataFrame(series).reindex(pd.date_range(day_one, to, freq="D")).ffill()
    sid = f"coinflip-{agent}"
    strategy = bt.Strategy(
        sid,
        [
            bt.algos.RunDaily(),
            _Seeded(n=max_positions, seed=make_seed(agent, day_one)),
            bt.algos.WeighEqually(),
            bt.algos.LimitWeights(1.0 / max(max_positions, 1)),
            bt.algos.Rebalance(),
        ],
    )
    result = bt.run(bt.Backtest(strategy, frame, initial_capital=initial))
    values = result.backtests[sid].strategy.values
    return {
        idx.date().isoformat(): float(v)
        for idx, v in values.items()
        if day_one <= idx.date().isoformat() <= to
    }


# ---------------------------------------------------------------------------
# One writer: every boundary it is the later writer of
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Measured:
    """One boundary. Returns are fractions; components are at the earlier date.

    ``gap`` is the seam as defined: published minus one-path return. The
    three components say where the two writers' values for the earlier date
    came apart (each a ratio minus one, multiplying to
    ``one-path value / published value`` at that date):

    - ``universe`` — the later writer's universe against the earlier one's,
      both on the later store: the path itself changed (a universe refresh).
    - ``late_close`` — closes the later store holds and the earlier one did
      not yet (a bar that landed after the earlier session ran, a hole
      filled): the one-day lag of a row priced before its own closes.
    - ``revision`` — closes both stores hold that differ (a vendor revision,
      a corporate-action rescale), or a change of code or price basis.
    """

    agent: str
    prev: str
    date: str
    writer: str
    prev_writer: str
    published_return: float
    one_path_return: float | None
    gap: float | None
    universe: float | None
    late_close: float | None
    revision: float | None
    reproduced: bool
    resolved: bool = True

    @property
    def splice(self) -> bool:
        """A seam with a path or a revision in it, not only a late close."""
        return any(
            c is not None and abs(c) > SEAM_ABS_TOL for c in (self.universe, self.revision)
        )


def _ratio(a: float | None, b: float | None) -> float | None:
    return (a / b - 1) if (a is not None and b) else None


def _measure_writer(args: tuple) -> list[Measured]:
    repo, writer, items, inputs, prev_inputs = args
    repo = Path(repo)

    def unmeasured(agent, b, published, resolved=False):
        return Measured(
            agent, b.prev, b.date, writer[:9], b.prev_writer[:9],
            published[b.date] / published[b.prev] - 1,
            None, None, None, None, None, False, resolved,
        )

    if "unresolved" in inputs:
        return [unmeasured(a, b, pub) for a, bs, pub in items for b in bs]
    blobs = _Blobs(repo)
    try:
        out: list[Measured] = []
        day_one = inputs["day_one"]
        later: dict[str, dict[str, float]] = {}
        held_before: dict[tuple[str, str], set[str]] = {}

        def later_closes(tickers):
            for t in tickers:
                if t not in later:
                    later[t] = _closes(
                        blobs.get(f"{writer}^:data/market/ohlcv/{t}.jsonl"),
                        day_one,
                        inputs["adjusted"],
                    )
            return later

        def earlier_dates(prev_writer, tickers, adjusted):
            for t in tickers:
                key = (prev_writer, t)
                if key not in held_before:
                    held_before[key] = set(
                        _closes(
                            blobs.get(f"{prev_writer}^:data/market/ohlcv/{t}.jsonl"),
                            day_one,
                            adjusted,
                        )
                    )
            return {t: held_before[(prev_writer, t)] for t in tickers}

        def run(tickers, max_pos, closes, to, agent):
            return pre16_coin_flip(
                agent, closes, tickers, max_pos, day_one, to, inputs["initial"]
            )

        for agent, bs, published in items:
            tickers = inputs["universes"].get(agent, [])
            max_pos = inputs["max_positions"].get(agent, 5)
            v = run(tickers, max_pos, later_closes(tickers), max(b.date for b in bs), agent)
            for b in bs:
                pub_ret = published[b.date] / published[b.prev] - 1
                vd, vp = v.get(b.date), v.get(b.prev)
                ok = vd is not None and math.isclose(
                    vd, published[b.date], rel_tol=REPRODUCE_REL_TOL, abs_tol=0.0
                )
                if not ok or vp is None:
                    out.append(unmeasured(agent, b, published, resolved=True))
                    continue
                one = vd / vp - 1
                prev = prev_inputs.get(b.prev_writer)
                universe = late = revision = None
                if prev is not None and "unresolved" not in prev:
                    p_tickers = prev["universes"].get(agent, [])
                    p_max = prev["max_positions"].get(agent, 5)
                    closes = later_closes(p_tickers)
                    if p_tickers == tickers and p_max == max_pos:
                        v_mix_p = vp
                    else:
                        v_mix_p = run(p_tickers, p_max, closes, b.prev, agent).get(b.prev)
                    held = earlier_dates(b.prev_writer, p_tickers, prev["adjusted"])
                    restricted = {
                        t: {d: c for d, c in closes[t].items() if d in held[t]}
                        for t in p_tickers
                    }
                    v_rev_p = run(p_tickers, p_max, restricted, b.prev, agent).get(b.prev)
                    universe = _ratio(vp, v_mix_p)
                    late = _ratio(v_mix_p, v_rev_p)
                    revision = _ratio(v_rev_p, published[b.prev])
                out.append(
                    Measured(
                        agent, b.prev, b.date, writer[:9], b.prev_writer[:9],
                        pub_ret, one, pub_ret - one, universe, late, revision, True,
                    )
                )
        return out
    finally:
        blobs.close()


#: The coin-flip states every restatement of the coin flips writes.
_STATE_PATHSPEC = "data/baselines/*/state/coinflip.json"


def pre_restatement(repo: Path, head: str = "HEAD") -> str:
    """The commit the audit measures by default: the parent of the oldest
    commit up to ``head`` whose subject declares ``[restate]`` and that wrote
    a coin-flip state, or ``head`` itself when there is none. A full sha."""
    log = _git(
        repo, "log", "--reverse", "--format=%H%x00%s", head, "--", _STATE_PATHSPEC
    )
    for line in log.splitlines():
        sha, _, subject = line.partition("\0")
        if subject.startswith("[restate]"):
            return _git(repo, "rev-parse", f"{sha}^").strip()
    return _git(repo, "rev-parse", head).strip()


def audit(
    repo: Path, tip: str, agents: list[str] | None = None, jobs: int = 1
) -> dict:
    tip_sha = _git(repo, "rev-parse", tip).strip()
    roster_agents = sorted(
        p.split("/")[2]
        for p in _git(repo, "ls-tree", "-r", "--name-only", tip_sha, "data/baselines").split()
        if re.fullmatch(r"data/baselines/[^/]+/coinflip\.json", p)
    )
    agents = [a for a in roster_agents if not agents or a in agents]
    if not agents:
        raise Unknown("no coin-flip series at the tip")
    by_writer: dict[str, list] = {}
    for agent in agents:
        published, writer = series_writers(repo, tip_sha, agent)
        bs = boundaries(repo, agent, published, writer)
        grouped: dict[str, list[Boundary]] = {}
        for b in bs:
            grouped.setdefault(b.writer, []).append(b)
        for w, items in grouped.items():
            by_writer.setdefault(w, []).append((agent, items, published))

    workdir = Path(tempfile.mkdtemp(prefix="coinflip-seams-"))
    try:
        epoch_cache: dict = {}
        all_writers = sorted(
            set(by_writer)
            | {b.prev_writer for items in by_writer.values() for _, bs, _ in items for b in bs}
        )
        inputs = {w: resolve_inputs(repo, f"{w}^", workdir, epoch_cache) for w in all_writers}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    tasks = []
    for w, items in sorted(by_writer.items()):
        prevs = {b.prev_writer for _, bs, _ in items for b in bs}
        tasks.append((str(repo), w, items, inputs[w], {p: inputs[p] for p in prevs}))
    measured: list[Measured] = []
    if jobs > 1 and len(tasks) > 1:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            for chunk in pool.map(_measure_writer, tasks):
                measured.extend(chunk)
    else:
        for t in tasks:
            measured.extend(_measure_writer(t))
    return summarise(agents, measured, tip_sha)


def summarise(agents: list[str], measured: list[Measured], tip: str) -> dict:
    total = len(measured)
    if total == 0:
        raise Unknown("no boundary between two writers was found to check")
    unresolved = [m for m in measured if not m.resolved]
    unreproduced = [m for m in measured if m.resolved and not m.reproduced]
    if len(unresolved) + len(unreproduced) > MAX_UNMEASURED_SHARE * total:
        raise Unknown(
            f"of {total} boundaries, {len(unresolved)} have a writer whose universe "
            f"could not be resolved and {len(unreproduced)} do not reproduce their "
            f"writer's own row; the method cannot speak to them"
        )
    seams = [m for m in measured if m.gap is not None and abs(m.gap) > SEAM_ABS_TOL]

    def tally(ms: list[Measured]) -> dict:
        splices = [m for m in ms if m.splice]
        big = max(ms, key=lambda m: abs(m.gap), default=None)
        big_splice = max(splices, key=lambda m: abs(m.gap), default=None)
        return {
            "seams": len(ms),
            "material_seams": sum(1 for m in ms if abs(m.gap) >= MATERIAL_GAP),
            "splices": len(splices),
            "material_splices": sum(1 for m in splices if abs(m.gap) >= MATERIAL_GAP),
            "universe_splices": sum(
                1 for m in splices if m.universe is not None and abs(m.universe) > SEAM_ABS_TOL
            ),
            "lag_only": len(ms) - len(splices),
            "largest": asdict(big) if big else None,
            "largest_splice": asdict(big_splice) if big_splice else None,
        }

    per_agent = {}
    for a in agents:
        mine = [m for m in measured if m.agent == a]
        per_agent[a] = {
            "boundaries": len(mine),
            "unresolved": sum(1 for m in mine if not m.resolved),
            "unreproduced": sum(1 for m in mine if m.resolved and not m.reproduced),
            **tally([m for m in seams if m.agent == a]),
        }
    return {
        "tip": tip,
        "boundaries": total,
        "unresolved": len(unresolved),
        "unreproduced": len(unreproduced),
        "unmeasured": [asdict(m) for m in unresolved + unreproduced],
        **tally(seams),
        "per_agent": per_agent,
        "all_seams": [
            dict(asdict(m), splice=m.splice)
            for m in sorted(seams, key=lambda m: (m.agent, m.date))
        ],
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def _describe(m: dict | None) -> str:
    if not m:
        return "-"
    return (
        f"{_pct(m['gap'])} on {m['prev']}->{m['date']} (published "
        f"{_pct(m['published_return'])}, one path {_pct(m['one_path_return'])}; "
        f"universe {_pct(m['universe'])}, late close {_pct(m['late_close'])}, "
        f"revision {_pct(m['revision'])})"
    )


def render(report: dict) -> str:
    lines = [
        f"Coin-flip splice seams at {report['tip'][:9]}",
        f"  seam: |published - one-path return| > {SEAM_ABS_TOL:g}; material: >= "
        f"{MATERIAL_GAP * 100:g} pp; splice: a seam with a universe or revision "
        f"component (not only a late close)",
        "",
        f"{'agent':20s} {'bounds':>6s} {'unres':>5s} {'unrep':>5s} {'seams':>5s} "
        f"{'>=.5pp':>6s} {'splice':>6s} {'>=.5pp':>6s} {'univ':>5s} {'lag':>4s}",
    ]
    for a, r in report["per_agent"].items():
        lines.append(
            f"{a:20s} {r['boundaries']:>6d} {r['unresolved']:>5d} {r['unreproduced']:>5d} "
            f"{r['seams']:>5d} {r['material_seams']:>6d} {r['splices']:>6d} "
            f"{r['material_splices']:>6d} {r['universe_splices']:>5d} {r['lag_only']:>4d}"
        )
    lines += ["", "Largest splice per agent:"]
    for a, r in report["per_agent"].items():
        lines.append(f"  {a:20s} {_describe(r['largest_splice'])}")
    lines += [
        "",
        f"Total: {report['boundaries']} boundaries between two writers "
        f"({report['unresolved']} unresolved, {report['unreproduced']} unreproduced); "
        f"{report['seams']} seams ({report['material_seams']} >= {MATERIAL_GAP * 100:g} pp), "
        f"of which {report['splices']} splices ({report['material_splices']} >= "
        f"{MATERIAL_GAP * 100:g} pp, {report['universe_splices']} with a universe "
        f"component) and {report['lag_only']} late-close only.",
        f"Largest seam:   {report['largest']['agent'] if report['largest'] else '-'} "
        f"{_describe(report['largest'])}",
        f"Largest splice: {report['largest_splice']['agent'] if report['largest_splice'] else '-'} "
        f"{_describe(report['largest_splice'])}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=_PROJECT_ROOT)
    parser.add_argument(
        "--at",
        "--tip",
        dest="at",
        help="the commit to audit (default: the last commit before the first "
        "coin-flip [restatement], see the module docstring)",
    )
    parser.add_argument("--agent", action="append", help="limit to these agents")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--json", type=Path, help="also write the full report here")
    args = parser.parse_args(argv)
    try:
        repo = args.repo.resolve()
        at = args.at or pre_restatement(repo)
        report = audit(repo, at, args.agent, args.jobs)
    except Exception as exc:  # noqa: BLE001 — any crash is an unknown, never a 1
        print(f"UNKNOWN: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 2
    print(render(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
