"""The coin-flip restatement, on a planted git history.

Live-only (LIVE_ONLY_TESTS in scripts/sync_core.py): the script reads this
desk's git history and is not shipped to midas-core.

A real git repository is built in which each "session" commits one coin-flip
row, and a universe refresh between two sessions adds ``DDD``. The fixture
repository carries its own tiny ``engine/universes`` (one JSON file per
universe), because the script resolves a writer's universe with that writer's
own code, extracted from git.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import restate_coinflip as rc  # noqa: E402

from engine.baselines import (  # noqa: E402
    advance_coin_flip,
    coin_flip_state_path,
    load_coin_flip_state,
    write_coin_flip_state,
)
from engine.config import get_config, reset_config_cache  # noqa: E402

AGENT = "probe"
ANCHOR = "probe-restate"
DAYS = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(11)]
#: Ten published rows; the store holds one more day, for the advance after.
PUBLISHED = DAYS[:10]
PRICES = {
    "AAA": [100, 104, 99, 107, 111, 103, 108, 115, 109, 112, 114],
    "BBB": [50, 49, 53, 55, 51, 57, 60, 58, 62, 61, 63],
    "CCC": [20, 22, 21, 19, 23, 24, 22, 26, 25, 27, 26],
    # Priced from day one, but in the universe only from the refresh on.
    "DDD": [80, 78, 85, 90, 88, 84, 92, 95, 91, 97, 99],
}
U1 = ["AAA", "BBB", "CCC"]
U2 = ["AAA", "BBB", "CCC", "DDD"]
REFRESH_AT = 5  # rows DAYS[5:] are first written after the refresh

ROSTER = """\
globals:
  day_one: '2026-01-01'
  initial_capital: 10000.0
  global_reference: {label: MSCI World, ticker: URTH, currency: USD}
agents:
  probe:
    display_name: Probe
    max_positions: 2
    universe: [u-probe]
    benchmark: {label: Probe, ticker: AAA, currency: USD}
    role: trader
"""

UNIVERSES_CODE = """\
import json, os
from pathlib import Path


def resolve_universe(name):
    root = Path(os.environ["MIDAS_DATA_DIR"])
    return json.loads((root / "data" / "universes" / f"{name}.json").read_text())
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


class Desk:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "roster.yaml").write_text(ROSTER)
        (repo / "METHODOLOGY.md").write_text(f'<a id="{ANCHOR}"></a>\n')
        (repo / "engine" / "universes").mkdir(parents=True)
        (repo / "engine" / "__init__.py").write_text("")
        (repo / "engine" / "universes" / "__init__.py").write_text(UNIVERSES_CODE)
        ohlcv = repo / "data" / "market" / "ohlcv"
        ohlcv.mkdir(parents=True)
        for t, closes in PRICES.items():
            (ohlcv / f"{t}.jsonl").write_text(
                "\n".join(json.dumps({"date": d, "close": float(c)}) for d, c in zip(DAYS, closes))
                + "\n"
            )
        self.series = repo / "data" / "baselines" / AGENT / "coinflip.json"
        self.series.parent.mkdir(parents=True)
        self.rows: list[dict] = []
        self.set_universe(U1)
        self.setup_sha = self.commit("chore: desk")
        self.writers: dict[str, str] = {}

    def set_universe(self, tickers: list[str]) -> None:
        path = self.repo / "data" / "universes" / "u-probe.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(tickers))

    def commit(self, msg: str) -> str:
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", msg)
        return _git(self.repo, "rev-parse", "HEAD")

    def session(self, day: str) -> str:
        """Publish ``day``'s row (any number: it is what gets restated)."""
        v = 10_000.0 if not self.rows else 10_000.0 + 7.0 * len(self.rows)
        self.rows.append(
            {"date": day, "portfolio_value": v, "cash": 0.0, "positions_value": v,
             "currency": "USD"}
        )
        self.series.write_text(json.dumps(self.rows, indent=2) + "\n")
        sha = self.commit(f"chore: weekday session {day}")
        self.writers[day] = sha
        return sha


@pytest.fixture
def desk(tmp_path, monkeypatch) -> Desk:
    d = Desk(tmp_path / "repo")
    for i, day in enumerate(PUBLISHED):
        if i == REFRESH_AT:
            d.set_universe(U2)
            d.commit("[data] universe refresh")
        d.session(day)
    # A later commit rewrites every row (a restatement): it must never pick
    # the universe, or U2 would reach the rows written under U1.
    d.rows = d.rows[:1] + [
        dict(r, portfolio_value=r["portfolio_value"] + 1.0) for r in d.rows[1:]
    ]
    d.series.write_text(json.dumps(d.rows, indent=2) + "\n")
    d.commit("[restate] rewrite every row")
    monkeypatch.setenv("MIDAS_DATA_DIR", str(d.repo))
    reset_config_cache()
    monkeypatch.setattr(rc, "PERIODS", (rc.Period(DAYS[0], None, rc.WRITER, rc.WRITER, "test"),))
    # The real era commit is not in a synthetic repo: the desk's own first
    # commit stands in, so every tree here is of the registry's era.
    monkeypatch.setattr(rc, "REGISTRY_INTRODUCED", d.setup_sha)
    yield d
    reset_config_cache()


def _main(desk: Desk, *argv: str) -> int:
    return rc.main(["--repo", str(desk.repo), *argv])


def _restated(desk: Desk, tmp_path: Path, periods=None) -> rc.Restated:
    resolver = rc.TreeResolver(desk.repo, tmp_path / "work")
    return rc.restate_agent(desk.repo, "HEAD", AGENT, periods or rc.PERIODS, resolver)


def _holdings_by_day(desk: Desk, tmp_path: Path, periods=None) -> dict[str, set[str]]:
    """Replay with the script's own inputs and return each day's holdings."""
    resolver = rc.TreeResolver(desk.repo, tmp_path / "work2")
    inputs = rc.schedule(
        desk.repo, "HEAD", AGENT, f"data/baselines/{AGENT}/coinflip.json",
        PUBLISHED, periods or rc.PERIODS, resolver,
    )
    closes = rc._Closes(sorted(set(PRICES)), "USD")
    states = rc.replay(
        AGENT, None, 10_000.0, PUBLISHED, inputs, closes=closes, frozen={}
    )
    return {s.date: set(s.holdings) for s in states}


class TestPointInTimeUniverse:
    def test_the_first_writer_picks_the_universe(self, desk, tmp_path):
        writers = rc.first_writer(desk.repo, "HEAD", f"data/baselines/{AGENT}/coinflip.json")
        assert writers == desk.writers  # not the [restate] commit that rewrote them

    def test_a_ticker_never_held_before_it_enters_the_universe(self, desk, tmp_path):
        held = _holdings_by_day(desk, tmp_path)
        before = [d for d in PUBLISHED[:REFRESH_AT] if "DDD" in held[d]]
        after = [d for d in PUBLISHED[REFRESH_AT:] if "DDD" in held[d]]
        assert before == []
        # The probe could pass by never drawing DDD at all; it is drawn.
        assert after, "DDD is never drawn once it is in the universe"

    def test_a_fixed_period_rev_overrides_the_writer_tree(self, desk, tmp_path):
        # The 04-17..04-29 shape: an encoded revision, not the writer's tree.
        periods = (
            rc.Period(DAYS[0], DAYS[7], desk.setup_sha, desk.setup_sha, "fixed"),
            rc.Period(DAYS[8], None, rc.WRITER, rc.WRITER, "writer"),
        )
        held = _holdings_by_day(desk, tmp_path, periods)
        assert all("DDD" not in held[d] for d in PUBLISHED[:8])

    def test_a_date_no_period_covers_is_unknown(self, desk, tmp_path, monkeypatch):
        monkeypatch.setattr(rc, "PERIODS", (rc.Period(DAYS[1], None, rc.WRITER, rc.WRITER, "x"),))
        before = desk.series.read_bytes()
        assert _main(desk) == 2
        assert desk.series.read_bytes() == before

    def test_an_unreadable_period_tree_is_unknown(self, desk, tmp_path, monkeypatch):
        # A revision whose tree holds no universe files: 2, never a guess.
        empty = _git(desk.repo, "commit-tree", _git(desk.repo, "hash-object", "-t", "tree", "/dev/null", "-w"), "-m", "empty")
        monkeypatch.setattr(rc, "PERIODS", (rc.Period(DAYS[0], None, empty, empty, "x"),))
        assert _main(desk) == 2
        assert not coin_flip_state_path(desk.series).exists()


class TestGate:
    def test_the_dry_run_writes_nothing(self, desk, capsys):
        before = desk.series.read_bytes()
        assert _main(desk) == 0
        assert desk.series.read_bytes() == before
        assert not coin_flip_state_path(desk.series).exists()
        out = capsys.readouterr().out
        assert "Dry run: nothing written" in out
        assert AGENT in out

    def test_a_dry_run_with_concerns_is_a_finding_exit_1(self, desk, capsys):
        """Regression: round-6 review, 2026-10-06. A dry run that found
        concerns ("--apply would refuse") exited 0, the code of a healthy
        run. It is a finding: 1. A clean dry run stays 0, ``Unknown`` 2."""
        for t in PRICES:
            (desk.repo / "data" / "market" / "ohlcv" / f"{t}.jsonl").write_text(
                "\n".join(json.dumps({"date": d, "close": 1e9}) for d in DAYS) + "\n"
            )
        assert _main(desk) == 1
        assert "--apply would refuse" in capsys.readouterr().out

    def test_apply_without_an_anchor_refuses(self, desk, capsys):
        before = desk.series.read_bytes()
        assert _main(desk, "--apply") == 2
        assert _main(desk, "--apply", "--changelog-entry", "no-such-anchor") == 2
        assert capsys.readouterr().err.count("REFUSED:") == 2
        assert desk.series.read_bytes() == before
        assert not coin_flip_state_path(desk.series).exists()

    def test_apply_writes_series_and_a_chaining_state(self, desk):
        assert _main(desk, "--apply", "--changelog-entry", ANCHOR) == 0
        rows = json.loads(desk.series.read_text())
        state = load_coin_flip_state(coin_flip_state_path(desk.series))
        assert [r["date"] for r in rows] == PUBLISHED
        assert rows[0]["portfolio_value"] == 10_000.0  # the first published row, kept
        assert (state.date, state.portfolio_value) == (rows[-1]["date"], rows[-1]["portfolio_value"])


class TestRestartInvariance:
    def test_split_replay_through_a_persisted_state_equals_one_replay(self, desk, tmp_path):
        resolver = rc.TreeResolver(desk.repo, tmp_path / "w")
        inputs = rc.schedule(
            desk.repo, "HEAD", AGENT, f"data/baselines/{AGENT}/coinflip.json",
            PUBLISHED, rc.PERIODS, resolver,
        )
        closes = rc._Closes(sorted(set(PRICES)), "USD")
        whole = rc.replay(AGENT, None, 10_000.0, PUBLISHED, inputs, closes=closes, frozen={})
        for k in range(1, len(PUBLISHED)):
            head = rc.replay(AGENT, None, 10_000.0, PUBLISHED[:k], inputs[:k], closes=closes, frozen={})
            path = tmp_path / f"state-{k}.json"
            write_coin_flip_state(path, head[-1], AGENT)
            tail = rc.replay(
                AGENT, load_coin_flip_state(path), 10_000.0, PUBLISHED[k:], inputs[k:],
                closes=closes, frozen={},
            )
            assert head + tail == whole, f"split at {PUBLISHED[k]}"

    def test_the_session_advance_continues_the_restated_path(self, desk, tmp_path):
        assert _main(desk, "--apply", "--changelog-entry", ANCHOR) == 0
        advance = advance_coin_flip(
            AGENT, U2, "USD", 2, desk.series,
            date.fromisoformat(DAYS[0]), date.fromisoformat(DAYS[10]),
        )
        assert advance.appended == 1 and advance.concerns == []
        appended = json.loads(desk.series.read_text())[-1]
        # The same day stepped by one uninterrupted replay over eleven days.
        resolver = rc.TreeResolver(desk.repo, tmp_path / "w")
        inputs = rc.schedule(
            desk.repo, "HEAD", AGENT, f"data/baselines/{AGENT}/coinflip.json",
            PUBLISHED, rc.PERIODS, resolver,
        )
        inputs.append(rc.DayInputs(tuple(U2), 2, "next"))
        closes = rc._Closes(sorted(set(PRICES)), "USD")
        whole = rc.replay(AGENT, None, 10_000.0, DAYS, inputs, closes=closes, frozen={})
        assert appended == rc._row(whole[-1], "USD")


def test_the_current_desk_config_is_the_one_read(desk):
    assert get_config().data_dir == desk.repo.resolve()


class TestEmptyDraw:
    """Regression: round-4 review, 2026-10-06. The replay kept its own copy of
    the empty-draw test (`_unpriceable`), with rules of its own; it now reads
    `_step`'s (`_EmptyDraw`), and refuses only the case the session carries."""

    def _inputs(self, universes: list[list[str]]) -> list:
        return [rc.DayInputs(tuple(u), 1, "w") for u in universes]

    def test_a_held_book_with_nothing_to_draw_is_unknown(self, desk):
        closes = rc._Closes(["AAA", "GONE"], "USD")
        with pytest.raises(rc.Unknown, match="could sell"):
            rc.replay(
                AGENT, None, 10_000.0, DAYS[:2], self._inputs([["AAA"], ["GONE"]]),
                closes=closes, frozen={},
            )

    def test_an_all_cash_book_with_nothing_to_draw_steps_in_cash(self, desk):
        closes = rc._Closes(["GONE"], "USD")
        states = rc.replay(
            AGENT, None, 10_000.0, DAYS[:2], self._inputs([["GONE"], ["GONE"]]),
            closes=closes, frozen={},
        )
        assert [s.cash for s in states] == [10_000.0, 10_000.0]


def test_the_two_writer_rules_are_shared_named_and_differ(desk):
    """Round-5 review, 2026-10-06. The restatement and the seam audit each
    carried their own copy of the git walk, and the restatement's docstring
    called its rule "the same convention" as the audit's, which it is not.
    Both now import one module that names both rules: the restatement takes
    the FIRST writer (no look-ahead), the audit the LAST (its control must
    reproduce the value the tip publishes). On a history whose last commit
    rewrote rows 2..10, the two disagree on exactly those rows."""
    import audit_coinflip_seams as audit

    from scripts import _coinflip_history as history

    rel = f"data/baselines/{AGENT}/coinflip.json"
    first = history.first_writer(desk.repo, "HEAD", rel)
    last = history.last_writer(desk.repo, "HEAD", rel)
    head = _git(desk.repo, "rev-parse", "HEAD")
    assert first == desk.writers
    assert last == {PUBLISHED[0]: desk.writers[PUBLISHED[0]], **{d: head for d in PUBLISHED[1:]}}
    assert rc.first_writer is history.first_writer
    assert audit.last_writer is history.last_writer
    assert "same convention" not in (rc.__doc__ or "")


@pytest.mark.parametrize("name", ["restate_coinflip.py", "audit_coinflip_seams.py"])
def test_the_script_names_no_bare_python3_interpreter(name):
    """Round-5 review, 2026-10-06 (the audit script, round 6). The scripts
    opened with ``#!/usr/bin/env python3``, which resolves to an interpreter
    outside the project venv. Like the other engine-importing scripts, they
    have no shebang and are not executable: they are run with
    ``.venv/bin/python``."""
    import os

    path = ROOT / "scripts" / name
    assert not path.read_text().startswith("#!")
    assert not os.access(path, os.X_OK)


def test_any_crash_exits_2_with_its_traceback(desk, monkeypatch, capsys):
    """Regression: round-5 review, 2026-10-06. ``main`` caught only
    ``Unknown``: any other exception escaped as Python's exit 1, the code a
    finding takes. It is a 2, could not run, with the traceback on stderr."""

    def boom(*args, **kwargs):
        raise RuntimeError("planted crash")

    monkeypatch.setattr(rc, "restate_agent", boom)
    before = desk.series.read_bytes()
    assert _main(desk) == 2
    err = capsys.readouterr().err
    assert "Traceback (most recent call last)" in err and "planted crash" in err
    assert "UNKNOWN: RuntimeError: planted crash" in err
    assert desk.series.read_bytes() == before


# ---------------------------------------------------------------------------
# Round-5 review, 2026-10-06: the instrument registry, point in time
# ---------------------------------------------------------------------------

REGISTRY = "data/market/instrument_status.json"


def _registry_doc(*symbols: str) -> str:
    return json.dumps(
        {
            "schema": 1,
            "instruments": {
                s: {"status": "suspended", "since": DAYS[0], "source": "test", "reason": "planted"}
                for s in symbols
            },
        }
    )


def _desk_with(tmp_path, monkeypatch, before_session) -> Desk:
    """The ``desk`` history, with ``before_session(desk, i)`` run before the
    session that first writes row ``i``."""
    d = Desk(tmp_path / "repo")
    for i, day in enumerate(PUBLISHED):
        if i == REFRESH_AT:
            d.set_universe(U2)
            d.commit("[data] universe refresh")
        before_session(d, i)
        d.session(day)
    monkeypatch.setenv("MIDAS_DATA_DIR", str(d.repo))
    reset_config_cache()
    monkeypatch.setattr(rc, "PERIODS", (rc.Period(DAYS[0], None, rc.WRITER, rc.WRITER, "test"),))
    monkeypatch.setattr(rc, "REGISTRY_INTRODUCED", d.setup_sha)
    return d


def _replay_rows(desk: Desk, tmp_path: Path, excluded: set[str]) -> list[dict]:
    work = tmp_path / f"w-replay-{len(list(tmp_path.glob('w-replay-*')))}"
    inputs = rc.schedule(
        desk.repo, "HEAD", AGENT, f"data/baselines/{AGENT}/coinflip.json",
        PUBLISHED, rc.PERIODS, rc.TreeResolver(desk.repo, work),
    )
    inputs = [
        rc.DayInputs(i.tickers, i.max_positions, i.writer, frozenset(excluded)) for i in inputs
    ]
    closes = rc._Closes(sorted(set(PRICES)), "USD")
    states = rc.replay(
        AGENT, None, 10_000.0, PUBLISHED, inputs, closes=closes, frozen={}
    )
    return [rc._row(s, "USD") for s in states]


class TestPointInTimeRegistry:
    def test_todays_registry_never_reaches_back_over_earlier_rows(self, desk, tmp_path):
        """Regression: round-5 review, 2026-10-06. The replay excluded what
        TODAY's registry marks on every past date. AAA, suspended only in the
        working tree now, was never in any writer's registry: the restated
        path is the one with nothing excluded."""
        (desk.repo / REGISTRY).write_text(_registry_doc("AAA"))
        restated = _restated(desk, tmp_path)
        assert restated.rows == _replay_rows(desk, tmp_path, set())
        # The control: excluding AAA moves the path, so the probe can fail.
        assert restated.rows != _replay_rows(desk, tmp_path, {"AAA"})
        assert restated.concerns == []

    def test_a_suspension_cleared_since_still_excludes_the_dates_it_covered(
        self, tmp_path, monkeypatch
    ):
        """DDD is suspended in the trees that wrote rows 5..7 and cleared
        before row 8: excluded on exactly those dates, although today's
        registry is empty."""
        introduced: list[str] = []

        def plant(d: Desk, i: int) -> None:
            if i == REFRESH_AT:
                (d.repo / REGISTRY).write_text(_registry_doc("DDD"))
                introduced.append(d.commit("feat: registry"))
            if i == 8:
                (d.repo / REGISTRY).write_text(_registry_doc())
                d.commit("chore(data): clear DDD")

        d = _desk_with(tmp_path, monkeypatch, plant)
        monkeypatch.setattr(rc, "REGISTRY_INTRODUCED", introduced[0])
        inputs = rc.schedule(
            d.repo, "HEAD", AGENT, f"data/baselines/{AGENT}/coinflip.json",
            PUBLISHED, rc.PERIODS, rc.TreeResolver(d.repo, tmp_path / "w"),
        )
        assert [sorted(i.excluded) for i in inputs] == [
            ["DDD"] if REFRESH_AT <= k < 8 else [] for k in range(len(PUBLISHED))
        ]
        assert all(i.registry_problem is None for i in inputs)

    @pytest.mark.parametrize("broken", ["unreadable", "missing"])
    def test_a_registry_that_fails_closed_in_a_writer_tree_is_a_concern(
        self, tmp_path, monkeypatch, broken
    ):
        """After the registry existed, a writer tree whose registry is
        unreadable or missing fails closed (every symbol excluded on its
        dates), as engine.instrument_status does, and says so."""
        introduced: list[str] = []

        def plant(d: Desk, i: int) -> None:
            if i == 2:
                (d.repo / REGISTRY).write_text(_registry_doc())
                introduced.append(d.commit("feat: registry"))
            if i == 4:
                if broken == "unreadable":
                    (d.repo / REGISTRY).write_text("{not json")
                else:
                    (d.repo / REGISTRY).unlink()
                    # A tripwire refusal writes a quarantine row: a registry
                    # missing beside one is lost, not a fresh data root.
                    quarantine = d.repo / "data" / "market" / "quarantine"
                    quarantine.mkdir(parents=True, exist_ok=True)
                    (quarantine / "2026-01-02.jsonl").write_text("{}\n")
                d.commit("break the registry")

        d = _desk_with(tmp_path, monkeypatch, plant)
        monkeypatch.setattr(rc, "REGISTRY_INTRODUCED", introduced[0])
        restated = _restated(d, tmp_path)
        registry = [c for c in restated.concerns if REGISTRY in c]
        assert len(registry) == 1
        concern = registry[0]
        assert "failing closed" in concern
        assert f"6 date(s), {PUBLISHED[4]}..{PUBLISHED[9]}" in concern


class TestRegistryRound6:
    """Round-6 review, 2026-10-06: the registry verdict must not fail open."""

    def test_an_unresolvable_era_commit_is_unknown_not_pre_registry(
        self, tmp_path, monkeypatch, capsys
    ):
        """Regression: round-6 review. With ``REGISTRY_INTRODUCED`` absent from
        the clone (shallow, pruned), every tree looked pre-registry and
        excluded nothing, silently. It is ``Unknown``: exit 2."""
        d = _desk_with(tmp_path, monkeypatch, lambda d, i: None)
        monkeypatch.setattr(rc, "REGISTRY_INTRODUCED", "0" * 40)
        with pytest.raises(rc.Unknown, match="registry era commit"):
            rc.TreeResolver(d.repo, tmp_path / "w").registry("HEAD")
        assert _main(d) == 2
        assert "UNKNOWN" in capsys.readouterr().err

    def test_a_missing_registry_beside_no_quarantine_is_an_empty_registry(
        self, tmp_path, monkeypatch
    ):
        """Regression: round-6 review. The engine reads a missing registry as
        empty unless a quarantine row exists beside it; the replay called any
        missing one a failure."""

        def plant(d: Desk, i: int) -> None:
            if i == 2:
                (d.repo / REGISTRY).write_text(_registry_doc())
                d.commit("feat: registry")
            if i == 4:
                (d.repo / REGISTRY).unlink()
                d.commit("registry gone, nothing was ever quarantined")

        d = _desk_with(tmp_path, monkeypatch, plant)
        restated = _restated(d, tmp_path)
        assert not [c for c in restated.concerns if REGISTRY in c]

    def test_failing_dates_are_printed_as_contiguous_runs(self, tmp_path, monkeypatch):
        """Regression: round-6 review. ``first..last`` over dates that were
        not contiguous claimed the dates between them failed too."""

        def plant(d: Desk, i: int) -> None:
            if i == 2:
                (d.repo / REGISTRY).write_text(_registry_doc())
                d.commit("feat: registry")
            if i in (4, 8):
                (d.repo / REGISTRY).write_text("{not json")
                d.commit("break the registry")
            if i == 6:
                (d.repo / REGISTRY).write_text(_registry_doc())
                d.commit("repair the registry")

        d = _desk_with(tmp_path, monkeypatch, plant)
        concern = [c for c in _restated(d, tmp_path).concerns if REGISTRY in c][0]
        broken = [PUBLISHED[k] for k in (4, 5, 8, 9)]
        assert f"{broken[0]}..{broken[1]}, {broken[2]}..{broken[3]}" in concern
        assert f"{PUBLISHED[4]}..{PUBLISHED[9]}" not in concern

    def test_the_verdict_is_computed_once_per_tree_rev(self, tmp_path, monkeypatch):
        """Regression: round-6 review. Every date re-ran the registry's git
        calls even for a fixed-rev period that supplies one tree to all."""
        d = _desk_with(tmp_path, monkeypatch, lambda d, i: None)
        resolver = rc.TreeResolver(d.repo, tmp_path / "w")
        calls: list[str] = []
        real = rc._rev
        monkeypatch.setattr(rc, "_rev", lambda repo, spec: (calls.append(spec), real(repo, spec))[1])
        first = resolver.registry("HEAD")
        n = len(calls)
        assert n > 0
        assert resolver.registry("HEAD") == first
        assert len(calls) == n


class TestApplyRefusesConcerns:
    """Regression: round-5 review, 2026-10-06. ``--apply`` published
    whatever the replay produced, concerns and all. It now refuses (exit 2,
    nothing written) while any concern stands; there is no flag to accept
    them."""

    def _assert_refused(self, d: Desk, capsys) -> str:
        before = d.series.read_bytes()
        assert _main(d, "--apply", "--changelog-entry", ANCHOR) == 2
        captured = capsys.readouterr()
        assert "REFUSED" in captured.err and "[WARN]" in captured.out
        assert d.series.read_bytes() == before
        assert not coin_flip_state_path(d.series).exists()
        return captured.out

    def test_an_unreadable_registry_refuses_and_writes_nothing(
        self, tmp_path, monkeypatch, capsys
    ):
        introduced: list[str] = []

        def plant(d: Desk, i: int) -> None:
            if i == 2:
                (d.repo / REGISTRY).write_text("{not json")
                introduced.append(d.commit("feat: a broken registry"))

        d = _desk_with(tmp_path, monkeypatch, plant)
        monkeypatch.setattr(rc, "REGISTRY_INTRODUCED", introduced[0])
        self._assert_refused(d, capsys)

    def test_a_path_that_never_leaves_cash_refuses(self, desk, capsys):
        # Every close now dearer than the whole book: nothing is ever bought.
        for t in PRICES:
            (desk.repo / "data" / "market" / "ohlcv" / f"{t}.jsonl").write_text(
                "\n".join(json.dumps({"date": d, "close": 1e9}) for d in DAYS) + "\n"
            )
        assert "never leaves cash" in self._assert_refused(desk, capsys)

    def test_frozen_and_carried_holdings_refuse(self, desk, capsys):
        # AAA is the only name the store prices, bought on day one; its close
        # is 0 from day two, so it is frozen at its mark (NO_PRICE_DATA) and
        # every later date has no candidate while the book holds only it.
        ohlcv = desk.repo / "data" / "market" / "ohlcv"
        (ohlcv / "AAA.jsonl").write_text(
            "\n".join(json.dumps({"date": d, "close": 1.0 if d == DAYS[0] else 0.0}) for d in DAYS)
            + "\n"
        )
        for t in ("BBB", "CCC", "DDD"):
            (ohlcv / f"{t}.jsonl").unlink()
        out = self._assert_refused(desk, capsys)
        assert "AAA NO_PRICE_DATA" in out
        assert "held only carried positions (AAA)" in out

    def test_a_clean_replay_still_applies(self, desk):
        assert _main(desk, "--apply", "--changelog-entry", ANCHOR) == 0
