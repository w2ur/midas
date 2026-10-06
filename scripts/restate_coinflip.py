"""Restate every published coin flip on one continuous, converted path.

The published ``data/baselines/<agent>/coinflip.json`` series before plan 1.6
are splices (``scripts/audit_coinflip_seams.py``): each row was frozen from the
universe and store its own session had, with one RNG reused across days, and
multi-currency books summed native closes unconverted. This script replays each
series, from its first published date to its last, with the corrected engine
(``engine.baselines._step``: a per-date seed over sorted candidates, every
amount converted at the valuation date's rate), and re-initialises the state
at the end of the replayed path so the next advance chains from it with no
seam.

``build_all_baselines`` keeps refusing a coin-flip scope: a routine restatement
must never touch a coin flip. This is the one dedicated, gated path.

**Point-in-time universe.** The universe of date D is the one the commit that
FIRST wrote row D ran with (``scripts._coinflip_history.first_writer``) — its
parent's tree, the inputs the writing session read. A later commit that
rewrote the row (a restatement) never picks the universe: that would let a
universe chosen later decide earlier picks. ``audit_coinflip_seams`` reads the
same ``<writer>^`` trees but attributes a row to its LAST writer, because its
control must reproduce the value the tip publishes; the two rules differ on
purpose, and the shared module names both. Membership is resolved by that
tree's OWN ``engine/universes`` code over that tree's OWN ``data/universes``
(some universes are lists in code, ``engine/universes/assets.py``, not files),
extracted from git and run with the network blocked; the agent -> universe-name
map and ``max_positions`` come from a ``roster.yaml``. Which tree supplies what
is encoded per period in ``PERIODS``, never discovered:

- 2026-04-17..04-29: membership from 3d4c8225c^, the first tree that commits
  universe files. Until 40ccf2fa6 (2026-04-29) membership was fetched from
  Wikipedia at runtime into an uncommitted cache, so git does not hold it: this
  window is the one look-ahead in the restated path (METHODOLOGY discloses it).
- 2026-04-30..06-29: membership from each first writer's tree; the map from the
  first committed ``roster.yaml`` (d84116712), generated from the
  ``AGENT_UNIVERSES`` / ``AGENT_MAX_POSITIONS`` constants these trees ran with.
- 2026-06-30 on: membership and map from each first writer's own tree.

**Prices and currencies are the current store's** and the current currency
resolution (``_Closes``), so a vendor revision made after a row was first
written is in the restated row. That is the method's known limit.

Dry run by default: prints, per agent, how many rows change, the largest
absolute and relative difference, and the published and restated value at the
last date. ``--apply`` requires ``--changelog-entry <anchor>`` (verified by
``engine.disclosure.require_changelog_entry``) and writes every series and its
state together, after every agent has been computed.

Run it with the project's interpreter, ``.venv/bin/python
scripts/restate_coinflip.py``. It carries no shebang, like the other scripts
that import this project's ``engine/`` (a ``uv run --script`` header declares
standalone dependencies, and this script's are the project's venv), and never a
bare ``python3``, which resolves to an interpreter this project does not use.

Exit codes: 0 done; 2 unknown — a date with no first writer, no period, a tree
or universe that cannot be resolved, a published series that is not one row
per calendar day, or a date on which nothing in the universe can be drawn
while the book holds names it could sell. Never a guess.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import yaml  # noqa: E402

from engine.baselines import (  # noqa: E402
    CoinFlipHolding,
    CoinFlipState,
    _Closes,
    _excluded,
    _frozen_concerns,
    _row,
    _EmptyDraw,
    _step,
    _thawed_notes,
    _write_json,
    coin_flip_state_path,
    write_coin_flip_state,
)
from engine.config import get_config  # noqa: E402
from engine.disclosure import require_changelog_entry  # noqa: E402
from engine.fx import store_cache  # noqa: E402
from scripts._coinflip_history import extract, first_writer, rev as _rev  # noqa: E402
from scripts._coinflip_history import git as _git  # noqa: E402

#: A period field that means "the first writer's own tree" (``<writer>^``).
WRITER = "<writer>^"


@dataclass(frozen=True)
class Period:
    """Which tree supplies a date's universe membership and its roster map.

    ``universes_rev`` holds ``engine/universes`` + ``data/universes``;
    ``roster_rev`` holds ``roster.yaml``. Each is a fixed revision or
    ``WRITER``. ``last`` None means open-ended.
    """

    first: str
    last: str | None
    universes_rev: str
    roster_rev: str
    why: str


#: The three periods (controller ruling, 2026-10-06). Encoded, not discovered.
PERIODS: tuple[Period, ...] = (
    Period(
        "2026-04-17",
        "2026-04-29",
        "3d4c8225c2e1a24cb68419a2b70416844334e311^",
        "d841167120a3558eb6b710e283e4c98a34b9000e",
        "membership fetched at runtime, uncommitted; first committed snapshot",
    ),
    Period(
        "2026-04-30",
        "2026-06-29",
        WRITER,
        "d841167120a3558eb6b710e283e4c98a34b9000e",
        "universe files committed, map in code; first committed roster.yaml",
    ),
    Period("2026-06-30", None, WRITER, WRITER, "roster.yaml and universes in tree"),
)

#: Every proxy variable pointed at a closed port: an old resolver that falls
#: back to the network on a missing file fails instead of fetching today's list.
_NO_NETWORK = {
    k: "http://127.0.0.1:9"
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY")
}

#: Run inside an extracted tree, with that tree's own universe code.
_RESOLVE = (
    "import json, os, sys; sys.path.insert(0, os.getcwd());"
    "from engine.universes import resolve_universe as r;"
    "print(json.dumps({n: sorted(set(r(n))) for n in json.loads(sys.argv[1])}))"
)


class Unknown(Exception):
    """The restatement cannot be computed without guessing (exit 2)."""


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def period_for(day: str, periods: tuple[Period, ...]) -> Period | None:
    for p in periods:
        if p.first <= day and (p.last is None or day <= p.last):
            return p
    return None


def _names(spec: dict) -> list[str]:
    u = spec.get("universe")
    if not u:
        return []
    return [u] if isinstance(u, str) else list(u)


class TreeResolver:
    """Resolves (universes rev, roster rev) to ``{agent: (tickers, max_positions)}``,
    cached by the trees involved, in an extracted copy of the universes tree."""

    def __init__(self, repo: Path, workdir: Path) -> None:
        self.repo = repo
        self.workdir = workdir
        self._cache: dict[tuple, dict[str, tuple[list[str], int]]] = {}

    def resolve(self, universes_rev: str, roster_rev: str) -> dict[str, tuple[list[str], int]]:
        code = _rev(self.repo, f"{universes_rev}:engine")
        files = _rev(self.repo, f"{universes_rev}:data/universes")
        roster_blob = _rev(self.repo, f"{roster_rev}:roster.yaml")
        if not (code and files and roster_blob):
            missing = [
                p
                for p, ok in (
                    (f"{universes_rev}:engine", code),
                    (f"{universes_rev}:data/universes", files),
                    (f"{roster_rev}:roster.yaml", roster_blob),
                )
                if not ok
            ]
            raise Unknown(f"cannot read {', '.join(missing)}")
        key = (code, files, roster_blob)
        if key in self._cache:
            return self._cache[key]
        roster = yaml.safe_load(_git(self.repo, "cat-file", "-p", roster_blob))
        agents = roster.get("agents") or {}
        names = sorted({n for spec in agents.values() for n in _names(spec)})
        target = self.workdir / f"t{len(self._cache)}"
        target.mkdir(parents=True)
        # A tree from 2026-06-29 on locates data/universes through its own
        # config, which loads its own roster.yaml: extract it when present.
        paths = ["engine", "data/universes"]
        if _rev(self.repo, f"{universes_rev}:roster.yaml"):
            paths.append("roster.yaml")
        extract(self.repo, universes_rev, paths, target)
        r = subprocess.run(
            [sys.executable, "-c", _RESOLVE, json.dumps(names)],
            cwd=target,
            capture_output=True,
            text=True,
            env={**os.environ, **_NO_NETWORK, "MIDAS_DATA_DIR": str(target)},
        )
        if r.returncode != 0:
            tail = r.stderr.strip().splitlines()[-1:] or ["failed"]
            raise Unknown(f"universes of {universes_rev} do not resolve: {tail[0][:200]}")
        members = json.loads(r.stdout.strip().splitlines()[-1])
        out: dict[str, tuple[list[str], int]] = {}
        for agent, spec in agents.items():
            if "max_positions" not in spec:
                continue
            out[agent] = (
                sorted({t for n in _names(spec) for t in members[n]}),
                int(spec["max_positions"]),
            )
        self._cache[key] = out
        return out


@dataclass(frozen=True)
class DayInputs:
    tickers: tuple[str, ...]
    max_positions: int
    writer: str


def schedule(
    repo: Path,
    tip: str,
    agent: str,
    relpath: str,
    dates: list[str],
    periods: tuple[Period, ...],
    resolver: TreeResolver,
) -> list[DayInputs]:
    """Each published date's universe and ``max_positions``, point in time."""
    writers = first_writer(repo, tip, relpath)
    out: list[DayInputs] = []
    for d in dates:
        w = writers.get(d)
        if w is None:
            raise Unknown(f"{agent} {d}: no commit wrote this row")
        p = period_for(d, periods)
        if p is None:
            raise Unknown(f"{agent} {d}: no period encodes where its universe comes from")
        urev = f"{w}^" if p.universes_rev == WRITER else p.universes_rev
        rrev = f"{w}^" if p.roster_rev == WRITER else p.roster_rev
        try:
            resolved = resolver.resolve(urev, rrev)
        except Unknown as exc:
            raise Unknown(f"{agent} {d} (writer {w[:9]}): {exc}") from None
        if agent not in resolved:
            raise Unknown(f"{agent} {d}: {rrev}:roster.yaml has no such agent")
        tickers, max_pos = resolved[agent]
        if not tickers:
            raise Unknown(f"{agent} {d}: empty universe at {urev}")
        out.append(DayInputs(tuple(tickers), max_pos, w))
    return out


# ---------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------


def replay(
    agent: str,
    start: CoinFlipState | None,
    initial: float,
    dates: list[str],
    inputs: list[DayInputs],
    *,
    closes: _Closes,
    excluded: set[str],
    frozen: dict[str, tuple[str, CoinFlipHolding]],
    thawed: dict[str, tuple[str, str]] | None = None,
) -> list[CoinFlipState]:
    """One path over ``dates``. With ``start`` None the first date starts from
    ``initial`` in cash and is repicked at its close (a fresh path's first day);
    otherwise every date steps from the previous state.

    A date with no candidate whose book held names it could sell is
    ``Unknown``: the session carries such a book untraded (``_step``), but a
    restatement that would publish a carried day is a judgment the owner
    makes, not one this script makes. A date with no candidate and nothing to
    sell steps in cash, as the session does: nothing is lost. The test is
    ``_step``'s own (``_EmptyDraw``), never a second copy of it."""
    states: list[CoinFlipState] = []
    state = start
    for d, inp in zip(dates, inputs):
        universe = list(inp.tickers)
        empty: list[_EmptyDraw] = []
        state = _step(
            agent,
            state.holdings if state is not None else {},
            state.cash if state is not None else initial,
            d,
            closes=closes,
            universe=universe,
            excluded=excluded,
            max_positions=inp.max_positions,
            frozen=frozen,
            empty=empty,
            thawed=thawed,
        )
        if any(e.held for e in empty):
            raise Unknown(
                f"{agent} {d}: nothing in its universe can be drawn ({empty[0].why}) "
                f"and the book holds names it could sell"
            )
        states.append(state)
    return states


@dataclass(frozen=True)
class Restated:
    agent: str
    series_path: Path
    rows: list[dict]
    state: CoinFlipState
    changed: int
    max_abs: float
    max_rel: float
    published_last: float
    restated_last: float
    last_date: str
    concerns: list[str]
    notes: list[str]


def restate_agent(
    repo: Path,
    tip: str,
    agent: str,
    periods: tuple[Period, ...],
    resolver: TreeResolver,
) -> Restated:
    cfg = get_config()
    spec = cfg.roster[agent].benchmark
    series_path = cfg.baselines_dir / agent / "coinflip.json"
    relpath = series_path.resolve().relative_to(repo.resolve()).as_posix()
    published = json.loads(series_path.read_text())
    if not published:
        raise Unknown(f"{agent}: no published row")
    dates = [r["date"] for r in published]
    start = date.fromisoformat(dates[0])
    expected = [(start + timedelta(days=i)).isoformat() for i in range(len(dates))]
    if dates != expected:
        raise Unknown(f"{agent}: the published series is not one row per calendar day")
    currency = spec.currency
    if {r.get("currency") for r in published} != {currency}:
        raise Unknown(f"{agent}: published currency is not the book currency {currency}")
    initial = published[0]["portfolio_value"]
    inputs = schedule(repo, tip, agent, relpath, dates, periods, resolver)
    union = sorted({t for i in inputs for t in i.tickers})
    closes = _Closes(union, currency)
    concerns: list[str] = []
    excluded = _excluded(agent, union, concerns)
    frozen: dict[str, tuple[str, CoinFlipHolding]] = {}
    thawed: dict[str, tuple[str, str]] = {}
    states = replay(
        agent, None, initial, dates, inputs,
        closes=closes, excluded=excluded, frozen=frozen, thawed=thawed,
    )
    concerns.extend(_frozen_concerns(agent, frozen, closes, currency))
    rows = [_row(s, currency) for s in states]
    if rows[0]["portfolio_value"] != initial:
        raise Unknown(f"{agent}: the restated first row does not keep {initial}")
    changed = sum(1 for a, b in zip(published, rows) if a != b)
    diffs = [
        (abs(b["portfolio_value"] - a["portfolio_value"]),
         abs(b["portfolio_value"] / a["portfolio_value"] - 1))
        for a, b in zip(published, rows)
    ]
    return Restated(
        agent,
        series_path,
        rows,
        states[-1],
        changed,
        max(d[0] for d in diffs),
        max(d[1] for d in diffs),
        published[-1]["portfolio_value"],
        rows[-1]["portfolio_value"],
        dates[-1],
        concerns,
        _thawed_notes(agent, thawed),
    )


def coinflip_agents() -> list[str]:
    cfg = get_config()
    return [
        a
        for a in cfg.trading_roster
        if cfg.roster[a].benchmark is not None
        and (cfg.baselines_dir / a / "coinflip.json").exists()
    ]


def render(results: list[Restated]) -> str:
    lines = [
        f"{'agent':20s} {'ccy':3s} {'rows':>4s} {'changed':>7s} {'max |diff|':>11s} "
        f"{'max rel':>8s} {'last date':10s} {'published':>10s} {'restated':>10s}"
    ]
    for r in results:
        lines.append(
            f"{r.agent:20s} {r.rows[0]['currency']:3s} {len(r.rows):>4d} {r.changed:>7d} "
            f"{r.max_abs:>11.2f} {r.max_rel * 100:>7.2f}% {r.last_date:10s} "
            f"{r.published_last:>10.2f} {r.restated_last:>10.2f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=_PROJECT_ROOT)
    parser.add_argument("--tip", default="HEAD")
    parser.add_argument("--agent", action="append", help="limit to these agents")
    parser.add_argument("--apply", action="store_true", help="write series and states")
    parser.add_argument("--changelog-entry", help="METHODOLOGY.md anchor (required with --apply)")
    args = parser.parse_args(argv)

    if args.apply:
        require_changelog_entry(
            args.changelog_entry, what="Restating every coin-flip series"
        )
    repo = args.repo.resolve()
    agents = [a for a in coinflip_agents() if not args.agent or a in args.agent]
    if not agents:
        print("UNKNOWN: no coin-flip series to restate", file=sys.stderr)
        return 2
    workdir = Path(tempfile.mkdtemp(prefix="restate-coinflip-"))
    try:
        resolver = TreeResolver(repo, workdir)
        # Each FX pair file is read once for the whole replay (engine.fx).
        with store_cache():
            results = [restate_agent(repo, args.tip, a, PERIODS, resolver) for a in agents]
    except Unknown as exc:
        print(f"UNKNOWN: {exc}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    for r in results:
        for c in r.concerns:
            print(f"  [WARN] {c}")
        for n in r.notes:
            print(f"  [INFO] {n}")
    print(render(results))
    if not args.apply:
        print("\nDry run: nothing written. --apply --changelog-entry <anchor> writes.")
        return 0
    for r in results:
        _write_json(r.series_path, r.rows)
        write_coin_flip_state(coin_flip_state_path(r.series_path), r.state, r.agent)
    print(f"\nWrote {len(results)} series and their states.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
