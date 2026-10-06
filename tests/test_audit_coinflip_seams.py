"""The coin-flip seam audit, falsified on a planted spliced history.

Live-only (LIVE_ONLY_TESTS in scripts/sync_core.py): the script reads this
desk's git history and is not shipped to midas-core.

A real git repository is built in which each "session" commits its store first
and its coin-flip row second, the way the desk's history reads (the writer's
inputs are its parent's tree). Universe resolution is the one thing stubbed:
the live run resolves each writer's universe with that writer's own code,
which a fixture repo has no copy of.
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

import audit_coinflip_seams as audit  # noqa: E402

AGENT = "probe"
DAY_ONE = "2026-01-01"
DAYS = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(10)]
PRICES = {
    "AAA": [100, 104, 99, 107, 111, 103, 108, 115, 109, 112],
    "BBB": [50, 49, 53, 55, 51, 57, 60, 58, 62, 61],
    "CCC": [20, 22, 21, 19, 23, 24, 22, 26, 25, 27],
    "DDD": [80, 78, 85, 90, 88, 84, 92, 95, 91, 97],
    "EEE": [10, 11, 13, 12, 14, 15, 13, 16, 17, 18],
}
U1 = ["AAA", "BBB", "CCC"]
U2 = ["AAA", "DDD", "EEE", "BBB"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


class Desk:
    """A fixture repository; each session commits its store, then its rows."""

    def __init__(self, root: Path) -> None:
        self.repo = root
        (root / "data" / "market" / "ohlcv").mkdir(parents=True)
        (root / "data" / "baselines" / AGENT).mkdir(parents=True)
        _git(root, "init", "-q", "-b", "main")
        _git(root, "config", "user.email", "t@t")
        _git(root, "config", "user.name", "t")
        self.rows: list[dict] = []
        self.inputs: dict[str, dict] = {}

    def _store(self, through: int, skip_last_for: set[str] = frozenset()) -> dict:
        closes = {}
        for t, series in PRICES.items():
            rows = [
                {"date": d, "close": float(c), "adj_close": float(c)}
                for i, (d, c) in enumerate(zip(DAYS, series))
                if i <= through and not (i == through and t in skip_last_for)
            ]
            (self.repo / "data" / "market" / "ohlcv" / f"{t}.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n"
            )
            closes[t] = {r["date"]: r["close"] for r in rows}
        return closes

    def session(self, day: int, universe: list[str], *, lag: bool = False) -> str:
        """Publish the row for ``DAYS[day]`` as a pre-1.6 session would: the
        whole path recomputed from day one on this session's store and
        universe, only the new date appended. ``lag``: the store does not
        hold this day's closes yet (a row priced before its own close)."""
        closes = self._store(day, skip_last_for=set(PRICES) if lag else set())
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "--allow-empty", "-m", f"data: store through {DAYS[day]}")
        path = audit.pre16_coin_flip(AGENT, closes, universe, 2, DAY_ONE, DAYS[day], 10_000.0)
        known = {r["date"] for r in self.rows}
        self.rows += [
            {"date": d, "portfolio_value": v, "cash": 0.0, "positions_value": v,
             "currency": "EUR"}
            for d, v in sorted(path.items())
            if d not in known
        ]
        (self.repo / "data" / "baselines" / AGENT / "coinflip.json").write_text(
            json.dumps(self.rows, indent=2) + "\n"
        )
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", f"chore: weekday session {DAYS[day]}")
        sha = _git(self.repo, "rev-parse", "HEAD")
        self.inputs[sha] = {
            "universes": {AGENT: list(universe)},
            "max_positions": {AGENT: 2},
            "day_one": DAY_ONE,
            "initial": 10_000.0,
            "adjusted": False,
        }
        return sha


@pytest.fixture
def desk(tmp_path, monkeypatch) -> Desk:
    d = Desk(tmp_path / "repo")
    monkeypatch.setattr(
        audit,
        "resolve_inputs",
        lambda repo, rev, workdir, cache: d.inputs[_git(repo, "rev-parse", rev.rstrip("^"))],
    )
    return d


def _by_date(report: dict) -> dict[str, dict]:
    return {m["date"]: m for m in report["all_seams"]}


def test_a_planted_universe_splice_is_found_and_a_clean_boundary_is_not(desk):
    desk.session(4, U1)           # rows 01-01..01-05 on U1
    desk.session(5, U2)           # universe refresh: row 01-06 from a U2 path
    desk.session(6, U2)           # same universe, store only appended: clean
    report = audit.audit(desk.repo, "HEAD", [AGENT])
    assert report["boundaries"] == 2 and report["unreproduced"] == 0
    seams = _by_date(report)
    assert set(seams) == {DAYS[5]}, "only the refresh boundary is a seam"
    splice = seams[DAYS[5]]
    assert splice["splice"] and abs(splice["universe"]) > 1e-6
    assert splice["late_close"] == pytest.approx(0.0, abs=1e-12)
    assert report["splices"] == 1 and report["lag_only"] == 0
    # The gap is what a reader of the published curve sees and no path did.
    assert splice["gap"] == pytest.approx(
        splice["published_return"] - splice["one_path_return"], abs=1e-15
    )


def test_a_row_priced_before_its_close_is_a_late_close_seam_not_a_splice(desk):
    desk.session(4, U1)
    desk.session(5, U1, lag=True)  # 01-06 written before 01-06's closes landed
    desk.session(6, U1)            # 01-07 written on a store holding 01-06
    report = audit.audit(desk.repo, "HEAD", [AGENT])
    seams = _by_date(report)
    assert set(seams) == {DAYS[6]}
    assert not seams[DAYS[6]]["splice"]
    assert abs(seams[DAYS[6]]["late_close"]) > 1e-6
    assert report["splices"] == 0 and report["lag_only"] == 1


def test_an_unspliced_history_has_no_seam(desk):
    """The control: the same sessions with one universe throughout."""
    for day in (4, 5, 6, 7):
        desk.session(day, U1)
    report = audit.audit(desk.repo, "HEAD", [AGENT])
    assert report["boundaries"] == 3 and report["seams"] == 0


def test_a_history_with_no_boundary_is_unknown(desk):
    desk.session(4, U1)
    with pytest.raises(audit.Unknown):
        audit.audit(desk.repo, "HEAD", [AGENT])


def test_rows_the_method_cannot_reproduce_make_the_audit_unknown(desk):
    """The control on the control: if the recomputation does not reproduce the
    writer's own row, the audit says it cannot measure rather than reporting
    seams (or their absence) it did not measure."""
    desk.session(4, U1)
    sha = desk.session(5, U2)
    desk.inputs[sha] = dict(desk.inputs[sha], universes={AGENT: U1[::-1]})
    with pytest.raises(audit.Unknown, match="do not reproduce"):
        audit.audit(desk.repo, "HEAD", [AGENT])


def test_main_exits_2_when_it_cannot_measure(desk, capsys):
    desk.session(4, U1)
    assert audit.main(["--repo", str(desk.repo), "--agent", AGENT, "--jobs", "1"]) == 2
    assert "UNKNOWN" in capsys.readouterr().err


def test_main_reports_and_exits_0_on_a_measured_history(desk, capsys, tmp_path):
    desk.session(4, U1)
    desk.session(5, U2)
    out = tmp_path / "r.json"
    assert audit.main(
        ["--repo", str(desk.repo), "--agent", AGENT, "--jobs", "1", "--json", str(out)]
    ) == 0
    assert "1 splices" in capsys.readouterr().out
    assert json.loads(out.read_text())["splices"] == 1


def _restate_on_one_path(desk: Desk, subject: str = "[restate] fix(baselines): one path") -> str:
    """Rewrite the published coin flip as one smooth path and write its
    state, as the 2026-10-06 restatement did; return the commit."""
    rows = [dict(r, portfolio_value=10_000.0 + i, positions_value=10_000.0 + i)
            for i, r in enumerate(desk.rows)]
    (desk.repo / "data" / "baselines" / AGENT / "coinflip.json").write_text(
        json.dumps(rows, indent=2) + "\n"
    )
    state = desk.repo / "data" / "baselines" / AGENT / "state" / "coinflip.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"date": rows[-1]["date"]}) + "\n")
    _git(desk.repo, "add", "-A")
    _git(desk.repo, "commit", "-q", "-m", subject)
    return _git(desk.repo, "rev-parse", "HEAD")


def test_by_default_it_audits_the_history_before_the_restatement(desk, capsys):
    """Regression: round-4 review, 2026-10-06. After the restatement rewrote
    every row on one path, the tree held no measurable boundary and the
    audit exited 2 ("unknown") on a correct tree, forever. By default it now
    measures the parent of the first [restate] commit that wrote a coin-flip
    state: the published history the METHODOLOGY entries quote."""
    desk.session(4, U1)
    before = desk.session(5, U2)
    _restate_on_one_path(desk)
    assert audit.pre_restatement(desk.repo) == before
    assert audit.main(["--repo", str(desk.repo), "--agent", AGENT, "--jobs", "1"]) == 0
    out = capsys.readouterr().out
    assert "1 splices" in out and before[:9] in out
    # The control: the restated tree itself cannot be measured, and says so.
    assert audit.main(
        ["--repo", str(desk.repo), "--agent", AGENT, "--jobs", "1", "--at", "HEAD"]
    ) == 2


def test_a_restate_commit_that_wrote_no_state_does_not_select(desk):
    """An earlier [restate] of the coin-flip rows alone (2026-08-07 rewrote
    them onto normalised units) is not the coin-flip restatement."""
    desk.session(4, U1)
    desk.session(5, U2)
    (desk.repo / "data" / "baselines" / AGENT / "coinflip.json").write_text(
        json.dumps(desk.rows, indent=1) + "\n"
    )
    _git(desk.repo, "commit", "-qam", "[restate] chore: units")
    head = _git(desk.repo, "rev-parse", "HEAD")
    assert audit.pre_restatement(desk.repo) == head


def test_a_missing_blob_whose_name_holds_a_space_reads_as_absent(desk):
    """Regression: the first live run crashed on `AMBU B.CO`, whose cat-file
    "missing" header splits into more than two fields."""
    desk.session(4, U1)
    blobs = audit._Blobs(desk.repo)
    try:
        assert blobs.get("HEAD:data/market/ohlcv/AMBU B.CO.jsonl") is None
        assert blobs.get("HEAD:data/market/ohlcv/AAA.jsonl")
    finally:
        blobs.close()
