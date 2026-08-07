"""The append-only CI gate, driven against real git repositories.

Live-only (see LIVE_ONLY_TESTS in scripts/sync_core.py): `check_append_only.py`
is live-repo CI infrastructure and is not in CORE_SCRIPTS.

The gate's whole value is discrimination, so every case below is a pair: the
thing it must catch, and the neighbouring thing it must not. A gate that fails
a weekend refresh or an ordinary session commit would be switched off within a
week, and then the incident it exists to catch happens anyway.

Calibrated against real history, not only these fixtures — see
`test_backtest_against_real_history`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "scripts" / "check_append_only.py"


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "data" / "portfolios" / "book").mkdir(parents=True)
    (r / "data" / "baselines" / "book").mkdir(parents=True)
    _run_git(r, "init", "-q", "-b", "main")
    _run_git(r, "config", "user.email", "t@t")
    _run_git(r, "config", "user.name", "t")
    return r


def _write_snapshots(repo: Path, rows: list[dict]) -> None:
    (repo / "data" / "portfolios" / "book" / "snapshots.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8"
    )


def _write_baseline(repo: Path, rows: list[dict]) -> None:
    (repo / "data" / "baselines" / "book" / "benchmark.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8"
    )


def _commit(repo: Path, message: str) -> None:
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", message)


def _gate(
    repo: Path, base: str = "HEAD^", head: str = "HEAD"
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), "--base", base, "--head", head],
        cwd=repo,
        capture_output=True,
        text=True,
    )


ROW_A = {
    "date": "2026-04-17",
    "session_date": "2026-04-17",
    "portfolio_value": 10_000.0,
    "cash": 10_000.0,
}
ROW_B = {
    "date": "2026-04-18",
    "session_date": "2026-04-18",
    "portfolio_value": 10_100.0,
    "cash": 9_000.0,
}


class TestSnapshots:
    def test_appending_a_new_row_passes(self, repo):
        """The ordinary session commit. Must never fire."""
        _write_snapshots(repo, [ROW_A])
        _commit(repo, "seed")
        _write_snapshots(repo, [ROW_A, ROW_B])
        _commit(repo, "chore: weekday session 2026-04-18")

        result = _gate(repo)
        assert result.returncode == 0, result.stdout

    def test_rewriting_a_published_row_fails(self, repo):
        """The 2026-08-03 incident: a later session overwrote an earlier row."""
        _write_snapshots(repo, [ROW_A])
        _commit(repo, "seed")
        _write_snapshots(repo, [dict(ROW_A, session_date="2026-04-20", cash=1.0)])
        _commit(repo, "chore: weekday session 2026-04-20")

        result = _gate(repo)
        assert result.returncode == 1
        assert "2026-04-17" in result.stdout
        assert "cash" in result.stdout

    def test_a_session_correcting_its_own_row_passes(self, repo):
        """A re-run fixing what it just wrote — what add_snapshot permits.

        The gate must not be stricter than the application, or it forbids a
        legitimate recovery path and gets disabled.
        """
        _write_snapshots(repo, [ROW_A])
        _commit(repo, "seed")
        _write_snapshots(repo, [dict(ROW_A, cash=9_999.0)])
        _commit(repo, "chore: weekday session 2026-04-17 (re-run)")

        result = _gate(repo)
        assert result.returncode == 0, result.stdout

    def test_a_legacy_row_without_session_date_fails_closed(self, repo):
        """Same choice add_snapshot makes: an unknown writer is not the same writer."""
        legacy = {"date": "2026-04-17", "portfolio_value": 10_000.0, "cash": 10_000.0}
        _write_snapshots(repo, [legacy])
        _commit(repo, "seed")
        _write_snapshots(repo, [dict(legacy, cash=1.0)])
        _commit(repo, "chore: weekday session 2026-04-20")

        assert _gate(repo).returncode == 1

    def test_deleting_a_published_row_fails(self, repo):
        _write_snapshots(repo, [ROW_A, ROW_B])
        _commit(repo, "seed")
        _write_snapshots(repo, [ROW_B])
        _commit(repo, "chore: weekday session 2026-04-19")

        result = _gate(repo)
        assert result.returncode == 1
        assert "row deleted" in result.stdout


class TestBaselines:
    def test_appending_passes(self, repo):
        _write_baseline(repo, [ROW_A])
        _commit(repo, "seed")
        _write_baseline(repo, [ROW_A, ROW_B])
        _commit(repo, "chore: weekend refresh 2026-04-18")

        assert _gate(repo).returncode == 0

    def test_retroactive_drift_fails(self, repo):
        """The defect append-or-refuse was added for: a revised close silently
        moving a published benchmark point under a frozen agent curve.

        Baselines are derived from the price series, so there is no "same
        writer" to appeal to — which is the conclusion the 2026-08-07
        coin-flip work reached.
        """
        row = {"date": "2026-04-17", "portfolio_value": 10_000.0, "currency": "EUR"}
        _write_baseline(repo, [row])
        _commit(repo, "seed")
        _write_baseline(repo, [dict(row, portfolio_value=10_050.0)])
        _commit(repo, "chore: weekday session 2026-04-20")

        result = _gate(repo)
        assert result.returncode == 1
        assert "benchmark.json" in result.stdout

    def test_the_same_session_exemption_does_not_leak_into_baselines(self, repo):
        """Scoped to snapshots.json by path, not to "has a session_date".

        Without this, a stray field appearing in a derived series would buy it
        an exemption it was never meant to have.
        """
        _write_baseline(repo, [ROW_A])
        _commit(repo, "seed")
        _write_baseline(repo, [dict(ROW_A, portfolio_value=10_050.0)])
        _commit(repo, "chore: weekday session 2026-04-20")

        assert _gate(repo).returncode == 1


class TestRestateDeclaration:
    def test_a_declared_restatement_passes(self, repo):
        _write_snapshots(repo, [ROW_A])
        _commit(repo, "seed")
        _write_snapshots(repo, [dict(ROW_A, session_date="2026-08-07", cash=1.0)])
        _commit(repo, "fix(data): restate valuations onto ISO units\n\n[restate]")

        result = _gate(repo)
        assert result.returncode == 0
        assert "declared" in result.stdout
        # Declared is not silent: the rows still get listed, and the changelog
        # obligation is restated. A bypass that prints nothing is a bypass
        # nobody reviews.
        assert "2026-04-17" in result.stdout
        assert "METHODOLOGY" in result.stdout

    def test_the_trailer_only_covers_the_commits_it_is_in(self, repo):
        """A [restate] three commits ago must not license today's rewrite."""
        _write_snapshots(repo, [ROW_A])
        _commit(repo, "seed")
        _write_snapshots(repo, [dict(ROW_A, session_date="2026-08-07", cash=2.0)])
        _commit(repo, "fix(data): a real restatement\n\n[restate]")
        _write_snapshots(repo, [dict(ROW_A, session_date="2026-08-08", cash=3.0)])
        _commit(repo, "chore: weekday session 2026-08-08")

        assert _gate(repo).returncode == 1


class TestOutOfScope:
    def test_unrelated_files_are_ignored(self, repo):
        (repo / "data" / "portfolios" / "book" / "portfolio.json").write_text("{}")
        _write_snapshots(repo, [ROW_A])
        _commit(repo, "seed")
        (repo / "data" / "portfolios" / "book" / "portfolio.json").write_text(
            json.dumps({"cash": 5.0})
        )
        _commit(repo, "chore: weekday session")

        assert _gate(repo).returncode == 0

    def test_a_brand_new_file_is_not_a_violation(self, repo):
        """A new agent's first snapshots.json has no frozen rows to protect."""
        _write_snapshots(repo, [ROW_A])
        _commit(repo, "seed")
        (repo / "data" / "portfolios" / "newbie").mkdir()
        (repo / "data" / "portfolios" / "newbie" / "snapshots.json").write_text(
            json.dumps([ROW_B])
        )
        _commit(repo, "chore: weekday session")

        assert _gate(repo).returncode == 0


def _has_deep_history(depth: int = 40) -> bool:
    """False on a shallow clone — CI checks out `fetch-depth: 1` for pytest."""
    try:
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"HEAD~{depth}^{{commit}}"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return False
    return True


class TestCannotEvaluateIsNotAViolation:
    """A crash must not read as a finding.

    This is how CI found the defect: on a shallow PR checkout `HEAD~1` does
    not resolve, git raised, the traceback exited 1, and exit 1 is the gate's
    "published row was rewritten" signal. The distinction gets its own exit
    code and its own tests.
    """

    def test_an_unresolvable_base_is_not_a_violation(self, repo):
        _write_snapshots(repo, [ROW_A])
        _commit(repo, "the very first commit")

        result = _gate(repo, base="HEAD^", head="HEAD")
        assert result.returncode == 0, result.stdout
        assert "nothing to compare" in result.stdout

    def test_an_unresolvable_head_is_reported_distinctly(self, repo):
        _write_snapshots(repo, [ROW_A])
        _commit(repo, "seed")

        result = _gate(repo, base="HEAD", head="refs/heads/does-not-exist")
        assert result.returncode == 2, result.stdout
        assert "::error::" in result.stdout

    def test_a_real_violation_still_exits_one(self, repo):
        """The control: exit 1 must still mean what it meant."""
        _write_snapshots(repo, [ROW_A])
        _commit(repo, "seed")
        _write_snapshots(repo, [dict(ROW_A, session_date="2026-04-20", cash=1.0)])
        _commit(repo, "chore: weekday session 2026-04-20")

        assert _gate(repo).returncode == 1


@pytest.mark.skipif(
    not _has_deep_history(),
    reason="shallow checkout — the real-history backtest needs 40 commits",
)
def test_backtest_against_real_history():
    """Replay the gate over this repo's own recent commits.

    Synthetic fixtures prove the logic; only real commits prove the
    calibration. Every commit the gate fires on here must be a genuine
    published-row mutation — if an ordinary session commit appears in this
    list, the gate is miscalibrated and would turn main red on Monday.

    Measured 2026-08-07 over the last 60 commits: six firings, all real. Three
    are the deliberate restatements of 2026-08-06/07. Three are ordinary
    session commits from BEFORE `merge_baseline_series` became append-or-refuse
    (2026-08-06) and before the snapshot-immutability fix (PR #19, 2026-08-04)
    — i.e. the gate is firing on exactly the defect those two fixes removed.

    The honest limitation: no ordinary session has run since both fixes landed
    (2026-08-06 was cancelled), so the first live weekday session is the real
    calibration test. This test asserts the known-bad set has not grown, which
    is what would happen if a new mutation route opened.
    """
    known_bad_subjects = (
        "fix(data): convert the store to ISO units",
        "fix(data): reconcile 23 mis-converted fills",
        "fix(data): restate published valuations",
        "chore: weekday session 2026-08-04",
        "chore: weekday session 2026-08-05",
        "Merge sandbox session claude/tender-ritchie-vzgvew",
        "Merge pull request #19 ",
    )

    fired: list[str] = []
    for i in range(1, 40):
        head = f"HEAD~{i - 1}"
        base = f"HEAD~{i}"
        try:
            subject = subprocess.run(
                ["git", "log", "-1", "--format=%s", head],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except subprocess.CalledProcessError:
            break
        result = _gate(REPO_ROOT, base=base, head=head)
        if result.returncode == 1:
            fired.append(subject)

    assert fired, "the gate fired on nothing in 40 commits — it cannot be working"
    unexpected = [
        subject
        for subject in fired
        if not any(subject.startswith(known) for known in known_bad_subjects)
    ]
    assert unexpected == [], (
        "the gate fired on commits not in the known-bad set — either a new "
        f"published-row mutation landed, or the gate is miscalibrated: {unexpected}"
    )
