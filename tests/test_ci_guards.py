"""Tests for CI infrastructure that has no other test.

Live-only (see LIVE_ONLY_TESTS in scripts/sync_core.py): this module reads
`.github/workflows/` and `backtester/`, neither of which exists in midas-core.

The theme is the standing rule these guards kept violating — a check that has
never produced the opposite answer is not evidence. The subjects here were all
green by never running, or by having no consumer:

* `backtester/tests` was outside `testpaths`, so `pytest -q` never collected it.
* `session-watchdog`'s detection piped `git log` into `grep -q`, which under
  `set -o pipefail` reports a SIGPIPE'd `git log` as the pipeline's status —
  so finding the commit made the check say it was missing.
* `.github/actions/failure-issue` is now the alerting path for five scheduled
  workflows, so its own branching gets exercised rather than assumed.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = REPO_ROOT / ".github" / "workflows" / "session-watchdog.yml"


# --------------------------------------------------------------------------
# W2.2 — backtester/tests is actually collected
# --------------------------------------------------------------------------


def test_backtester_tests_are_in_testpaths():
    """`pytest -q` must collect the backtester suite, not just tests/.

    pytest skips a testpaths entry that matches nothing *silently* (that is
    what lets this same pyproject.toml sync to midas-core, which ships no
    backtester). The silence is the hazard: renaming the directory would stop
    collecting 13 files with no diagnostic anywhere, which is exactly how they
    went unrun in the first place. Pin the entry and the directory together.
    """
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    testpaths = config["tool"]["pytest"]["ini_options"]["testpaths"]
    assert "backtester/tests" in testpaths

    collected = sorted(
        p.name for p in (REPO_ROOT / "backtester" / "tests").glob("test_*.py")
    )
    assert collected, "backtester/tests exists in testpaths but holds no test modules"
    # Named, not counted: a count assertion fails on every addition, which
    # trains people to bump the number without looking at what moved.
    assert "test_healthz.py" in collected
    assert "test_auth.py" in collected


# --------------------------------------------------------------------------
# W2.3 — session-watchdog detection actually detects
# --------------------------------------------------------------------------


def _watchdog_run_script() -> str:
    workflow = yaml.safe_load(WATCHDOG.read_text())
    steps = workflow["jobs"]["watchdog"]["steps"]
    scripts = [s["run"] for s in steps if "run" in s]
    assert len(scripts) == 1, "expected a single run block in session-watchdog"
    return scripts[0]


def test_watchdog_never_pipes_into_grep_q():
    """Regression: the SIGPIPE shape must not come back.

    `cmd | grep -q PATTERN` under `set -o pipefail` returns 141 when grep
    short-circuits before cmd finishes writing — a match reported as a
    non-match. Measured against this repo's history (2026-08-04, one matching
    subject present), the old form found the commit in 0 of 20 runs.
    """
    script = _watchdog_run_script()
    offenders = [
        line.strip()
        for line in script.splitlines()
        if not line.strip().startswith(
            "#"
        )  # the comment explaining the defect quotes it
        and re.search(r"\|\s*grep\b[^|]*\s-\w*q", line)
    ]
    assert offenders == [], f"pipeline into `grep -q` under pipefail: {offenders}"


DETECTION_START = 'echo "Looking for weekday session commit'


def _extract_detection_lines(script: str) -> tuple[list[str], str]:
    """Pull the live detection statements out of the workflow itself.

    Reimplementing them here would test this file, not the workflow. The
    region is delimited by markers rather than by the specific commands, so
    the extraction survives any shape the detection takes — including the
    single-pipeline form this replaced. That matters: an extractor that only
    understands the fixed shape would raise instead of failing when the defect
    came back, and "the test errored" is a weaker signal than "the test says
    the commit was not found".
    """
    lines = [line.strip() for line in script.splitlines()]
    start = next(i for i, line in enumerate(lines) if line.startswith(DETECTION_START))
    body: list[str] = []
    condition = ""
    for line in lines[start + 1 :]:
        if not line or line.startswith("#"):
            continue
        if line.startswith("if ") and line.endswith("; then"):
            condition = line[len("if ") : -len("; then")]
            break
        body.append(line)
    assert condition, "no `if ...; then` found after the detection marker"
    return body, condition


# The defect only manifests while `git log` is still walking history at the
# moment `grep -q` matches and exits — that is when the write hits a closed
# pipe. A three-commit repo finishes walking first and lets the broken form
# pass; measured under bash, ~30 commits with the match at the tip fails it
# every time, as does this repo's real history. Keep the filler.
_FILLER_COMMITS = 30


def _fixture_repo(tmp_path: Path, subjects: list[str], day: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    stamp = f"{day}T12:00:00+0000"
    env = {
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_DATE": stamp,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env=env)
    for i, subject in enumerate(subjects):
        (repo / f"f{i}.txt").write_text(subject)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
        subprocess.run(
            ["git", "commit", "-q", "-m", subject], cwd=repo, check=True, env=env
        )
    # The workflow greps `origin/main`; a bare fixture repo has no remote, so
    # point the remote-tracking ref at the branch we just built.
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=repo,
        check=True,
        env=env,
    )
    return repo


def _run_detection(repo: Path, tmp_path: Path, day: str) -> bool:
    body, condition = _extract_detection_lines(_watchdog_run_script())
    script = "\n".join(
        [
            "set -euo pipefail",
            f'yesterday="{day}"',
            'pattern="^chore: weekday session $yesterday"',
            *body,
            f"if {condition}; then exit 0; else exit 1; fi",
        ]
    )
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=repo,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(tmp_path),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode in (0, 1), result.stderr
    return result.returncode == 0


DAY = "2026-08-04"


@pytest.fixture(scope="module")
def present_repo(tmp_path_factory) -> Path:
    """A repo whose session commit is at the tip, behind enough history that
    `git log` is provably still walking when a matching `grep -q` exits."""
    tmp = tmp_path_factory.mktemp("present")
    subjects = [f"chore: filler {i}" for i in range(_FILLER_COMMITS)]
    subjects.append(f"chore: weekday session {DAY}")
    return _fixture_repo(tmp, subjects, DAY)


@pytest.fixture(scope="module")
def absent_repo(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("absent")
    subjects = [f"chore: filler {i}" for i in range(_FILLER_COMMITS)]
    subjects.append("docs: unrelated")
    return _fixture_repo(tmp, subjects, DAY)


@pytest.mark.parametrize("attempt", range(5))
def test_watchdog_detects_a_present_session_commit(present_repo, tmp_path, attempt):
    """The commit is there; the workflow's own lines must say so — every time.

    Repeated because what this replaces was a race, not a constant: a single
    green run would not have distinguished the two forms.
    """
    assert _run_detection(present_repo, tmp_path, DAY) is True


def test_watchdog_reports_a_genuinely_missing_session(absent_repo, tmp_path):
    """The control: with no session commit, detection must still say missing.

    Without this, a check hard-wired to `true` would pass the test above.
    """
    assert _run_detection(absent_repo, tmp_path, DAY) is False


# --------------------------------------------------------------------------
# W2.4 — the failure-issue action's branching
# --------------------------------------------------------------------------

FAILURE_ISSUE_ACTION = (
    REPO_ROOT / ".github" / "actions" / "failure-issue" / "action.yml"
)

# The workflows expected to route their outcome through the shared action.
# Named rather than counted: an addition should be a deliberate edit here, not
# a bumped integer.
ALERTING_WORKFLOWS = [
    "core-drift-guard.yml",
    "fetch-ohlcv.yml",
    "fetch-sentiment.yml",
    "refresh-universes.yml",
    "resweep-held-tickers.yml",
    # Not scheduled, but the same "a red X is not a consumer" problem: it runs
    # on every push to main, and on 2026-08-07 it went red on the session
    # commit with no issue filed and nothing to read but the X. Its three jobs
    # guard published data — the least visible place for silence to sit.
    "session-integrity.yml",
    # Added 2026-08-18. These four had no consumer at all, which is a W2.10
    # violation on the money path: `check-triggers` is the daily conditional
    # sweep and — since the crypto watcher became dispatch-only — the only
    # scheduled one; `attest-ledger` is the tamper-evidence job, where silence
    # means no claim either way; `refresh-leaderboard` writes the public
    # weekend artifact. All four granted `contents: write` only, and a
    # `permissions:` block is exhaustive, so the issue POST would have 403'd
    # and the wiring would have been decorative.
    "check-triggers.yml",
    "check-triggers-crypto.yml",
    "attest-ledger.yml",
    "refresh-leaderboard.yml",
    # The retry sweep that re-dispatches a waiting fallback branch between
    # the 13:00 watcher and the session. Its own failure — origin unlistable,
    # or a refused dispatch — means a fill has nothing left that will try to
    # merge it before the deadline, and nothing else can see that.
    "retry-fallback-merges.yml",
    # Added 2026-09-05 for its WATCHER half only: a `triggers/*` fallback
    # branch that never reaches main is a fill the ledger does not show, and
    # nothing else watches for it (session-watchdog covers the session half).
    # Its reporter is gated on the commit kind — see the workflow header and
    # TestAutoMergeTakesWatcherFallbackBranches.
    "auto-merge-session.yml",
]


def test_every_alerting_workflow_has_its_own_issue_identity():
    """Idempotency is keyed on the TITLE, so two causes must not share one.

    This is issue #37's lesson generalised across workflows rather than across
    one workflow's two modes: `failure-issue` closes an open issue whose title
    matches on the next SUCCESS, so if two workflows filed under one title, a
    green run of the healthy one would close the sick one's issue and the alert
    would disappear while the failure continued.
    """
    titles = {}
    for name in ALERTING_WORKFLOWS:
        workflow = yaml.safe_load(
            (REPO_ROOT / ".github" / "workflows" / name).read_text()
        )
        for job in workflow["jobs"].values():
            for step in job["steps"]:
                if step.get("uses") != "./.github/actions/failure-issue":
                    continue
                title = step["with"]["title"]
                assert title, f"{name} files an issue with no title"
                assert title not in titles, (
                    f"{name} and {titles[title]} both file under {title!r}; a "
                    "success in one would close the other's open issue"
                )
                titles[title] = name
    assert len(titles) >= len(ALERTING_WORKFLOWS), (
        "fewer titles than alerting workflows — the collector missed some"
    )


def _failure_issue_script() -> str:
    action = yaml.safe_load(FAILURE_ISSUE_ACTION.read_text())
    steps = action["runs"]["steps"]
    assert len(steps) == 1, "expected a single step in the failure-issue action"
    return steps[0]["run"]


def _run_failure_issue(tmp_path: Path, outcome: str, existing: str = "") -> list[str]:
    """Run the action's real script with `gh` stubbed out.

    Returns the gh sub-commands it invoked, in order — the observable the
    workflows depend on.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls = tmp_path / "gh-calls.txt"
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/bash\n"
        f'echo "$1 $2" >> "{calls}"\n'
        # `gh issue list` is the only call whose output the script reads.
        f'if [[ "$1 $2" == "issue list" ]]; then printf "%s" "{existing}"; fi\n'
        f'if [[ "$1 $2" == "issue create" ]]; then echo "https://example/issues/1"; fi\n'
        "exit 0\n"
    )
    gh.chmod(0o755)

    result = subprocess.run(
        ["bash", "-c", _failure_issue_script()],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "RUNNER_TEMP": str(tmp_path),
            "GH_TOKEN": "stub",
            "TITLE": "some-job: it broke",
            "BODY": "why it matters",
            "OUTCOME": outcome,
            "RUN_URL": "https://example/run/1",
        },
        capture_output=True,
        text=True,
    )
    # The action must never change a run's verdict — it reports on one.
    assert result.returncode == 0, result.stderr
    return calls.read_text().splitlines() if calls.exists() else []


def test_failure_files_an_issue_when_none_is_open(tmp_path):
    assert _run_failure_issue(tmp_path, "failure") == ["issue list", "issue create"]


def test_failure_comments_instead_of_filing_a_duplicate(tmp_path):
    """Idempotent per cause: a job failing for five days is one fact.

    Filing five issues for it is the alert fatigue this replaces.
    """
    assert _run_failure_issue(tmp_path, "failure", existing="42") == [
        "issue list",
        "issue comment",
    ]


def test_success_closes_a_previously_filed_issue(tmp_path):
    assert _run_failure_issue(tmp_path, "success", existing="42") == [
        "issue list",
        "issue close",
    ]


def test_success_with_no_open_issue_does_nothing(tmp_path):
    assert _run_failure_issue(tmp_path, "success") == ["issue list"]


def test_cancellation_is_not_an_alertable_failure(tmp_path):
    """A cancelled run is usually a human superseding it, not a breakage."""
    assert _run_failure_issue(tmp_path, "cancelled") == []


def test_alerting_workflows_report_their_outcome():
    """Every scheduled writer routes its outcome to the shared action.

    `core-drift-guard` was red three consecutive Mondays with no consumer;
    wiring is the whole fix, so wiring is what gets asserted.
    """
    for name in ALERTING_WORKFLOWS:
        workflow = yaml.safe_load(
            (REPO_ROOT / ".github" / "workflows" / name).read_text()
        )
        # The reporter may live in any job, not only the first: a multi-job
        # workflow reports once from a trailing job that `needs` the others,
        # because one red run is one fact and three issues for it is the
        # alert-fatigue machine this action exists to replace.
        reporters = [
            (job_name, s)
            for job_name, job in workflow["jobs"].items()
            for s in job["steps"]
            if s.get("uses") == "./.github/actions/failure-issue"
        ]
        assert len(reporters) == 1, f"{name} does not report its outcome exactly once"
        job_name, step = reporters[0]
        # `if: always()` — without it the step is skipped on the failure it
        # exists to report. A reporter may narrow it further (auto-merge-session
        # reports only for watcher branches, so a green session merge cannot
        # close the watcher's issue), but `always()` must come first: any
        # `&&` clause after it still evaluates on failure.
        assert (step.get("if") or "").startswith("always()"), (
            f"{name}'s reporter is conditional on success"
        )

        outcome = step["with"]["outcome"]
        guarded_jobs = set(workflow["jobs"]) - {job_name}
        if guarded_jobs:
            # A dedicated reporting job's own `job.status` is always success —
            # it would report green on every red run. It must aggregate the
            # jobs it watches, and must actually depend on all of them or it
            # races them to the finish.
            assert "needs.*.result" in outcome, (
                f"{name}'s reporter job reports its own status, not the jobs it watches"
            )
            declared = workflow["jobs"][job_name].get("needs") or []
            assert set(declared) == guarded_jobs, (
                f"{name}'s reporter waits on {sorted(declared)}, "
                f"not on {sorted(guarded_jobs)}"
            )
        else:
            assert outcome == "${{ job.status }}", f"{name} reports a hardcoded outcome"

        # `issues: write` at workflow level, or gh 403s and the alert is lost.
        assert workflow["permissions"]["issues"] == "write", (
            f"{name} cannot file issues"
        )


# --------------------------------------------------------------------------
# push-with-retry — staging paths that may not exist
# --------------------------------------------------------------------------

PUSH_WITH_RETRY_ACTION = (
    REPO_ROOT / ".github" / "actions" / "push-with-retry" / "action.yml"
)


def _push_with_retry_script() -> str:
    action = yaml.safe_load(PUSH_WITH_RETRY_ACTION.read_text())
    steps = action["runs"]["steps"]
    assert len(steps) == 1, "expected a single step in the push-with-retry action"
    return steps[0]["run"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture
def pushable_repo(tmp_path):
    """A clone with a real local `origin`, so the push path actually runs.

    Stubbing `git push` would leave the action's whole reason for existing —
    commit, push, rebase, retry — untested. A bare repo on disk is cheap.
    """
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    repo = tmp_path / "work"
    subprocess.run(
        ["git", "clone", str(remote), str(repo)], check=True, capture_output=True
    )
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "data").mkdir()
    (repo / "data" / "store").mkdir()
    (repo / "data" / "store" / "AAPL.jsonl").write_text('{"date": "2026-08-06"}\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    _git(repo, "push", "-u", "origin", "main")
    return repo


def _run_push(repo: Path, tmp_path: Path, paths: str) -> subprocess.CompletedProcess:
    output = tmp_path / "github-output.txt"
    output.touch()
    return subprocess.run(
        ["bash", "-c", _push_with_retry_script()],
        cwd=repo,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(tmp_path),
            "GITHUB_OUTPUT": str(output),
            "INPUT_PATHS": paths,
            "INPUT_MESSAGE": "[data] test commit",
            "INPUT_ATTEMPTS": "3",
        },
        capture_output=True,
        text=True,
    )


def test_absent_pathspec_does_not_kill_the_push(pushable_repo, tmp_path):
    """Regression: 5599e64f6 — `fetch-ohlcv` staged a directory that never exists.

    `data/market/quarantine/` is created only when the ingest tripwire refuses
    a row. On every healthy night it is absent, and `git add` exits 128 on a
    pathspec matching nothing while the `git status` guard above it exits 0.
    Two runs' worth of OHLCV was fetched and then discarded with the runner.
    """
    (pushable_repo / "data" / "store" / "MSFT.jsonl").write_text('{"date": "x"}\n')

    result = _run_push(
        pushable_repo, tmp_path, "data/store/ data/quarantine-that-does-not-exist/"
    )

    assert result.returncode == 0, result.stderr
    assert "MSFT.jsonl" in _git(pushable_repo, "show", "--stat", "origin/main")


def test_the_absent_path_is_the_only_thing_skipped(pushable_repo, tmp_path):
    """The present sibling must still be staged, not dropped with it."""
    (pushable_repo / "data" / "store" / "MSFT.jsonl").write_text('{"date": "x"}\n')

    result = _run_push(pushable_repo, tmp_path, "data/store/ data/nope/")

    assert "Skipping 'data/nope/'" in result.stdout
    assert "Skipping 'data/store/'" not in result.stdout


def test_all_paths_absent_is_nothing_to_commit_not_a_failure(pushable_repo, tmp_path):
    result = _run_push(pushable_repo, tmp_path, "data/nope/ data/also-nope/")

    assert result.returncode == 0, result.stderr
    assert "None of the requested paths exist" in result.stdout


def test_a_deleted_tracked_path_still_stages_its_deletion(pushable_repo, tmp_path):
    """Filtering on `-e` alone would silently skip a deletion.

    An absent path is normally "nothing to stage", but a *tracked* path that
    has been deleted is a real change `git add` handles fine. The filter keys
    on the index too, so removing a whole directory still commits.
    """
    subprocess.run(["rm", "-rf", str(pushable_repo / "data" / "store")], check=True)

    result = _run_push(pushable_repo, tmp_path, "data/store/")

    assert result.returncode == 0, result.stderr
    committed = _git(pushable_repo, "show", "--stat", "origin/main")
    assert "AAPL.jsonl" in committed


def test_the_absent_path_check_can_actually_fail(pushable_repo, tmp_path):
    """The control: without the filter, this is the 128 that broke fetch-ohlcv.

    Asserting only that the fixed action succeeds proves nothing about the bug
    it fixes — a green test here would stay green if the filter were deleted
    and the pathspec happened to exist. So run the *unfiltered* command the
    action used to run, and require it to blow up.
    """
    (pushable_repo / "data" / "store" / "MSFT.jsonl").write_text('{"date": "x"}\n')

    status = subprocess.run(
        ["git", "status", "--porcelain", "--", "data/store/", "data/nope/"],
        cwd=pushable_repo,
        capture_output=True,
        text=True,
    )
    add = subprocess.run(
        ["git", "add", "--", "data/store/", "data/nope/"],
        cwd=pushable_repo,
        capture_output=True,
        text=True,
    )

    assert status.returncode == 0, "git status is what made the guard pass"
    assert add.returncode != 0, "git add is what made the stage fail"
    assert "did not match any files" in add.stderr


@pytest.fixture
def shallow_pushable_repo(tmp_path):
    """A SHALLOW clone with a real local `origin` — what the runners get.

    Every caller checks out at `fetch-depth: 1`, so the shallow case is the
    only one that runs in production. The plain `pushable_repo` fixture clones
    at full depth and therefore cannot see the deepening at all.
    """
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "clone", str(remote), str(seed)], check=True, capture_output=True
    )
    _git(seed, "config", "user.email", "test@example.com")
    _git(seed, "config", "user.name", "test")
    (seed / "data").mkdir()
    (seed / "data" / "store").mkdir()
    for n in range(3):  # >1 commit, so depth 1 is genuinely shallow
        (seed / "data" / "store" / "AAPL.jsonl").write_text('{"date": "%d"}\n' % n)
        _git(seed, "add", "-A")
        _git(seed, "commit", "-m", f"seed {n}")
    _git(seed, "push", "-u", "origin", "main")

    repo = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "--depth", "1", f"file://{remote}", str(repo)],
        check=True,
        capture_output=True,
    )
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    assert _git(repo, "rev-parse", "--is-shallow-repository").strip() == "true"
    return repo, remote, seed


def test_a_clean_push_never_deepens_the_clone(shallow_pushable_repo, tmp_path):
    """The unshallow belongs to the retry path, and only to it.

    `git fetch --unshallow` measured ~110-127s and ~836 MB on this repo
    (2026-08-18) and ran up front on EVERY committing run of all four
    scheduled writers, to prepare for a rebase that nearly never happens —
    ~110 billed minutes a month against a 2,000-minute cap.

    Asserted behaviourally rather than by grepping the script for `--unshallow`:
    a grep would pass on a script that still deepened, as long as it did so
    under a different spelling.
    """
    repo, _remote, _seed = shallow_pushable_repo
    (repo / "data" / "store" / "MSFT.jsonl").write_text('{"date": "x"}\n')

    result = _run_push(repo, tmp_path, "data/store/")

    assert result.returncode == 0, result.stderr
    assert _git(repo, "rev-parse", "--is-shallow-repository").strip() == "true", (
        "a clean push deepened the clone — the unshallow is back before the "
        "first push attempt"
    )


def test_a_rejected_push_deepens_then_rebases_and_succeeds(
    shallow_pushable_repo, tmp_path
):
    """The retry path must still work from depth 1 — that is what it is for.

    A shallow clone cannot rebase onto a fetched main, so if the deepening is
    moved without being reinstated here, a collision stops being a retry and
    becomes a lost commit — the exact failure this action exists to prevent.
    """
    repo, _remote, seed = shallow_pushable_repo

    # Another writer lands first, exactly as a colliding scheduled job would.
    (seed / "data" / "store" / "OTHER.jsonl").write_text('{"date": "y"}\n')
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "the other writer")
    _git(seed, "push", "origin", "main")

    (repo / "data" / "store" / "MSFT.jsonl").write_text('{"date": "x"}\n')
    result = _run_push(repo, tmp_path, "data/store/")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Push rejected" in result.stdout, "the collision did not happen"
    assert _git(repo, "rev-parse", "--is-shallow-repository").strip() == "false", (
        "the retry path did not deepen; a shallow clone cannot rebase"
    )
    landed = _git(repo, "log", "--format=%s", "origin/main")
    assert "[data] test commit" in landed and "the other writer" in landed


def test_every_push_with_retry_caller_names_paths_that_can_be_checked():
    """Every caller's paths must be a plain space-separated list.

    The filter loop word-splits `$INPUT_PATHS`, so a path containing a space
    would silently split into two nonexistent ones and be skipped — the same
    silent data loss in a new costume.
    """
    workflows = (REPO_ROOT / ".github" / "workflows").glob("*.yml")
    callers = [
        step["with"]["paths"]
        for path in workflows
        for job in yaml.safe_load(path.read_text())["jobs"].values()
        for step in job.get("steps", [])
        if step.get("uses") == "./.github/actions/push-with-retry"
    ]
    assert callers, "no workflow uses push-with-retry — has it been renamed?"
    for paths in callers:
        for path in paths.split():
            assert not path.startswith("-"), f"{path!r} would parse as a git flag"
        assert paths.split() == paths.split(" "), (
            f"{paths!r} has repeated or padded separators; word-splitting is lossy"
        )


class TestFetchOhlcvScheduleSelectsTheMode:
    """Each declared cron must select the mode it was declared for.

    The gate read the runner's clock (`date -u +%u`: Mon-Fri full, Sat-Sun
    crypto-only) while the crons said Tue-Sat full / Sun-Mon crypto-only. So
    Saturday — the one run that carries Friday's equity closes — fired the
    full-universe cron and ran crypto-only, and Monday picked those closes up
    ~66 h late. Nothing failed; the partition simply moved out from under the
    gate, and this is that gate's first test.

    Asserted in BOTH directions. "Every cron is mapped" alone would pass a
    workflow carrying an arm for a cron nobody schedules any more, and "every
    arm is a real cron" alone would pass one that forgot a cron entirely and
    fell through to the default.
    """

    WORKFLOW = REPO_ROOT / ".github" / "workflows" / "fetch-ohlcv.yml"

    #: cron string -> is this run crypto-only?
    #:
    #: Hand-maintained ON PURPOSE. It is the statement of intent the workflow
    #: is checked against; deriving it from the workflow would assert nothing
    #: at all, which is precisely the state this class ends.
    EXPECTED = {
        "0 6 * * 2-6": False,  # Tue-Sat: full universe, carries the cash closes
        "0 6 * * 0,1": True,  # Sun-Mon: the two days no cash market trades
    }

    def _text(self) -> str:
        return self.WORKFLOW.read_text(encoding="utf-8")

    def _declared_crons(self) -> list[str]:
        return re.findall(r'^\s*-\s*cron:\s*"([^"]+)"', self._text(), re.M)

    def _fetch_step(self) -> dict:
        """The step that actually runs the script — not the file's prose.

        Read through the parser rather than off the raw text: the header
        discusses the clock test it replaced, so a whole-file grep for it
        would fire on the documentation of the fix.
        """
        spec = yaml.safe_load(self._text())
        steps = [s for job in spec["jobs"].values() for s in job["steps"]]
        fetch = [s for s in steps if s.get("id") == "fetch"]
        assert len(fetch) == 1, "fetch-ohlcv has no single `fetch` step"
        return fetch[0]

    def _case_arms(self) -> dict[str, bool]:
        arms = re.findall(
            r'^\s*"([^"]*)"\)\s*CRYPTO_ONLY=(true|false)',
            self._fetch_step()["run"],
            re.M,
        )
        return {pattern: value == "true" for pattern, value in arms}

    def test_every_declared_cron_has_an_arm(self):
        declared = self._declared_crons()
        assert declared, "fetch-ohlcv declares no schedule"
        assert set(declared) == set(self.EXPECTED), (
            f"declared crons {sorted(declared)} disagree with the intended "
            f"mapping {sorted(self.EXPECTED)} — update both together"
        )
        arms = self._case_arms()
        for cron in declared:
            assert cron in arms, (
                f"cron {cron!r} is scheduled but has no case arm; it would "
                "fall through to the default and run the full universe"
            )

    def test_each_cron_selects_its_intended_mode(self):
        arms = self._case_arms()
        for cron, crypto_only in self.EXPECTED.items():
            assert arms[cron] is crypto_only, (
                f"cron {cron!r} selects crypto_only={arms[cron]}, intended "
                f"{crypto_only}"
            )

    def test_no_arm_names_a_cron_nobody_schedules(self):
        """A stale arm is how the last mapping rotted: it keeps reading right."""
        declared = set(self._declared_crons())
        for pattern in self._case_arms():
            if pattern == "":  # workflow_dispatch, deliberately not a cron
                continue
            assert pattern in declared, (
                f"case arm {pattern!r} matches no declared cron — either the "
                "schedule was removed or the arm was mistyped"
            )

    def test_manual_dispatch_runs_the_full_universe(self):
        """`github.event.schedule` is empty on workflow_dispatch.

        Without an explicit arm it would hit the default, which is also full —
        but by accident rather than by decision, and a future default change
        would silently retarget every manual run.
        """
        assert self._case_arms().get("") is False

    def test_the_mode_is_not_keyed_on_the_runner_clock(self):
        """The regression itself: a clock read cannot express a cron partition.

        A run delayed past midnight — routine, the scheduler is late by 40 min
        to 2 h 45 typically and has a tail past 5 h — would flip its own mode.
        """
        step = self._fetch_step()
        assert step.get("env", {}).get("SCHEDULE") == "${{ github.event.schedule }}", (
            "the fetch step does not receive the schedule that fired it"
        )
        # Comments are stripped first: the step explains the clock test it
        # replaced, and a raw substring check would fire on that explanation.
        executed = "\n".join(
            line
            for line in step["run"].splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "date -u" not in executed, (
            "the mode is being derived from the runner's clock again"
        )


class TestFetchOhlcvAlertsPerMode:
    """`fetch-ohlcv` has two modes, so it needs two alert identities.

    `failure-issue` keys idempotency on the TITLE — deliberately, since a job
    failing five days running is one fact. But this workflow runs a full
    universe Tue-Sat and a ~35-symbol crypto subset Sun-Mon, and those are
    different facts about different data. With one shared title they collided:
    issue #37 was opened by the full-universe runs that quarantined MNST, BYND
    and JMAT.L on 08-13/14/15, and then CLOSED on 2026-08-16 — "Recovered: a
    later run of this job succeeded" — by the Sunday CRYPTO-ONLY run, which
    does not fetch a single one of those equities and could not have recovered
    anything. The next full run re-filed it. An alert that reports recovery on
    evidence incapable of observing the failure is worse than no alert: it is
    the alert-fatigue machine `failure-issue` was written to replace, rebuilt
    one layer up in the caller.

    The fix belongs in the caller, not the action: the action's contract
    ("title identifies the cause; must be stable across recurrences") was
    already right, and `fetch-ohlcv` was passing one title for two causes.
    """

    WORKFLOW = REPO_ROOT / ".github" / "workflows" / "fetch-ohlcv.yml"

    def _steps(self) -> list[dict]:
        spec = yaml.safe_load(self.WORKFLOW.read_text(encoding="utf-8"))
        return [s for job in spec["jobs"].values() for s in job["steps"]]

    def _fetch_step(self) -> dict:
        fetch = [s for s in self._steps() if s.get("id") == "fetch"]
        assert len(fetch) == 1, "fetch-ohlcv has no single `fetch` step"
        return fetch[0]

    def _alert_step(self) -> dict:
        alerts = [
            s
            for s in self._steps()
            if str(s.get("uses", "")).endswith("actions/failure-issue")
        ]
        assert len(alerts) == 1, "fetch-ohlcv has no single failure-issue step"
        return alerts[0]

    def test_the_fetch_step_publishes_the_mode_it_ran(self):
        """The alert cannot distinguish what the step never reports."""
        run = self._fetch_step()["run"]
        executed = "\n".join(
            line for line in run.splitlines() if not line.lstrip().startswith("#")
        )
        assert "mode=" in executed and "GITHUB_OUTPUT" in executed, (
            "the fetch step does not emit a `mode` output, so the alert has "
            "nothing to key on"
        )

    def test_the_alert_title_varies_with_the_mode(self):
        """A constant title is what let a crypto-only success close issue #37."""
        title = self._alert_step()["with"]["title"]
        assert "steps.fetch.outputs.mode" in title, (
            f"failure-issue title {title!r} is constant across both modes — a "
            "crypto-only success will close a full-universe failure again"
        )

    def test_the_title_survives_a_run_that_never_resolved_a_mode(self):
        """A failure before the fetch step must not render an empty identity.

        `if: always()` means the alert runs even when checkout died and
        `steps.fetch.outputs.mode` is the empty string. Without a fallback the
        title ends in `()`, which is a third, unnamed bucket that reads as a
        typo rather than as a cause.
        """
        title = self._alert_step()["with"]["title"]
        assert "||" in title, (
            f"failure-issue title {title!r} has no fallback for an unresolved mode"
        )


class TestPushGateMatchesExitCodes:
    """Both callers of fetch_ohlcv.py must gate their push on its exit code.

    Lives here rather than beside the script's own tests because it reads
    `.github/workflows/`, which does not exist in midas-core — a
    workflow-reading test in a synced module is an unconditional failure in the
    public repo's suite, and `sync_core check` cannot see it (the file is
    byte-identical in both repos; that is precisely the problem). Found by
    running core's suite after the sync.
    """

    WORKFLOWS = ["fetch-ohlcv.yml", "resweep-held-tickers.yml"]

    @pytest.mark.parametrize("workflow", WORKFLOWS)
    def test_the_push_is_gated_on_a_committable_exit(self, workflow):
        """`resweep-held-tickers` needs this most: `_apply_split_to_holders`
        has already persisted a share/cost-basis correction by the time the
        failure-rate exit fires, and a skipped commit discards it for a week."""
        spec = yaml.safe_load(
            (REPO_ROOT / ".github" / "workflows" / workflow).read_text()
        )
        steps = [s for job in spec["jobs"].values() for s in job["steps"]]

        push = [
            s for s in steps if s.get("uses") == "./.github/actions/push-with-retry"
        ]
        assert len(push) == 1, f"{workflow} does not push exactly once"
        assert "steps.fetch.outputs.committable == 'true'" in push[0].get("if", ""), (
            f"{workflow} commits without consulting the script's exit code"
        )

    @pytest.mark.parametrize("workflow", WORKFLOWS)
    def test_the_shell_mapping_matches_the_python_constants(self, workflow):
        """Otherwise the two halves of the contract drift apart silently.

        A deliberate exit the shell forgets to list stops committing; an
        unhandled traceback the shell wrongly lists starts committing a
        half-written store.
        """
        from scripts.fetch_ohlcv import COMMITTABLE_EXITS

        text = (REPO_ROOT / ".github" / "workflows" / workflow).read_text()
        listed = re.search(r"^\s*([0-9|]+)\)\s*echo \"committable=true\"", text, re.M)
        assert listed, f"{workflow} has no committable-exit case arm"

        assert {int(c) for c in listed.group(1).split("|")} == set(COMMITTABLE_EXITS), (
            f"{workflow}'s case arm {listed.group(1)!r} disagrees with "
            f"COMMITTABLE_EXITS {COMMITTABLE_EXITS}"
        )


def test_every_push_with_retry_caller_path_resolves_in_the_repo():
    """A skipped path is now silent, so the paths themselves need a guard.

    Filtering absent pathspecs fixed a hard failure but replaced it with an
    exit-0 skip that no caller reads (`pushed` is consumed nowhere) and that
    `failure-issue` cannot see, because the job stays green. If a caller's
    directory is renamed, it would quietly stop committing forever.

    `data/market/quarantine/` used to be exempted here — the one caller path
    that could legitimately be absent, since it only materialised once the
    ingest tripwire actually refused a row. It fired for the first time in
    `761c60382` (2026-08-11, a quarantined MNST print), so the directory is
    committed now and can never be absent again; the exemption (and its
    control test, `test_the_optional_path_really_is_absent`) are retired with
    it rather than left as a set with nothing in it — an exemption mechanism
    that iterates zero live entries is a dark guard, not a stricter one. None
    of the other three `push-with-retry` callers (`fetch-sentiment`,
    `refresh-universes`, `resweep-held-tickers`) stage a path with the same
    shape. If one ever does, reintroduce a named exemption set here for it —
    don't resurrect an empty one preemptively.
    """
    workflows = (REPO_ROOT / ".github" / "workflows").glob("*.yml")
    callers = [
        (path.name, step["with"]["paths"])
        for path in workflows
        for job in yaml.safe_load(path.read_text())["jobs"].values()
        for step in job.get("steps", [])
        if step.get("uses") == "./.github/actions/push-with-retry"
    ]
    assert callers, "no workflow uses push-with-retry — has it been renamed?"
    for workflow, paths in callers:
        for path in paths.split():
            assert (REPO_ROOT / path).exists(), (
                f"{workflow} stages {path!r}, which does not exist — the action "
                "will skip it silently and the job will stay green"
            )


# ---------------------------------------------------------------------------
# Regression-comment citations (2026-08-07 review, W6 meta-finding 2)
# ---------------------------------------------------------------------------

_REGRESSION_CITE = re.compile(r"Regression:\s*([0-9a-f]{7,40})\b")


def _regression_citations() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for path in sorted((REPO_ROOT / "tests").glob("test_*.py")):
        for sha in _REGRESSION_CITE.findall(path.read_text(encoding="utf-8")):
            out.append((path, sha))
    return out


def _is_shallow() -> bool:
    return (REPO_ROOT / ".git" / "shallow").exists()


class TestRegressionCitations:
    """`// Regression: <hash> — <bug>` is the portfolio's convention, and a
    convention with no check drifts: `test_ohlcv_ingest.py`'s crypto
    partial-bar test cited `63970d933`, the unrelated XSS/universe PR, for a
    year of readers to follow into the wrong commit.

    **Scope limit, stated rather than hidden.** The resolution check needs
    history, and CI checks out at `fetch-depth: 1` — with 2.5 GB of committed
    price store behind this repo, a full-depth clone on every push is not a
    trade worth making for this. So the shape check runs everywhere and the
    resolution check runs on a full clone, i.e. for whoever writes the
    comment, at the moment they write it. That is the useful moment; it is
    not the same as a CI gate, and this docstring is where that is admitted.
    """

    def test_citations_exist_at_all(self) -> None:
        """The control. A parser that finds nothing passes both tests below
        forever."""
        assert len(_regression_citations()) >= 5

    def test_every_citation_is_shaped_like_a_sha(self) -> None:
        """Always runs, shallow clone or not."""
        for path, sha in _regression_citations():
            assert re.fullmatch(r"[0-9a-f]{7,40}", sha), f"{path.name}: {sha!r}"

    @pytest.mark.skipif(_is_shallow(), reason="shallow clone has no history to resolve")
    def test_every_citation_resolves_to_a_real_commit(self) -> None:
        unresolved = [
            f"{path.name}: {sha}"
            for path, sha in _regression_citations()
            if subprocess.run(
                ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                cwd=REPO_ROOT,
                capture_output=True,
            ).returncode
            != 0
        ]
        assert not unresolved, (
            "regression comments cite commits that do not exist in this "
            f"repository: {unresolved}"
        )

    @pytest.mark.skipif(_is_shallow(), reason="shallow clone has no history to resolve")
    def test_the_resolver_can_fail(self) -> None:
        """Control for the test above — `git cat-file -e` must actually
        reject a hash, not return 0 for anything hex-shaped."""
        assert (
            subprocess.run(
                [
                    "git",
                    "cat-file",
                    "-e",
                    "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef^{commit}",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
            ).returncode
            != 0
        )


# ---------------------------------------------------------------------------
# backtester/requirements.txt mirrors the root lockfile
# ---------------------------------------------------------------------------
#
# The service image installs a NARROWED package set (it imports only bt,
# fastapi, uvicorn, pandas, pandas_ta, pydantic, yaml and yfinance), but every
# version in it is copied verbatim from the root lockfile so the image runs
# exactly what CI tested. That contract lived only in a comment at the top of
# the file, and it had already been broken once: until 2026-08-05 four
# transitive packages (markdown-it-py, mdurl, pygments, rich) were absent from
# the list and therefore installed UNPINNED into a deployed service — precisely
# the reproducibility hole the root lockfile exists to close.

_ROOT = Path(__file__).resolve().parents[1]
_PIN = re.compile(r"^([A-Za-z0-9._-]+)==(.+)$")

# Every lockfile DERIVED from the root one by narrowing. Each may drop packages;
# none may re-resolve a version. `requirements-watcher.txt` joined on 2026-08-17
# — check-triggers-crypto.yml installs it hourly on the money path, so a version
# skew there is a watcher firing trades against a stack the desk never tested.
_DERIVED_LOCKFILES = ("backtester/requirements.txt", "requirements-watcher.txt")


def _normalise(name: str) -> str:
    """PEP 503 normalisation — `curl_cffi` and `curl-cffi` are one package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        bare = line.split("#")[0].strip()
        match = _PIN.match(bare)
        if match:
            pins[_normalise(match.group(1))] = match.group(2).strip()
    return pins


def _unpinned_requirement_lines(path: Path) -> list[str]:
    """Requirement lines that are not an exact `==` pin."""
    loose = []
    for line in path.read_text(encoding="utf-8").splitlines():
        bare = line.split("#")[0].strip()
        if not bare or bare.startswith("-"):
            continue
        if not _PIN.match(bare):
            loose.append(bare)
    return loose


class TestBacktesterLockfileMirror:
    @pytest.mark.parametrize("derived", _DERIVED_LOCKFILES)
    def test_every_derived_pin_matches_the_root_lockfile(self, derived):
        """The narrowing may drop packages; it may never re-resolve versions."""
        root = _pins(_ROOT / "requirements.txt")
        service = _pins(_ROOT / derived)
        assert service, f"{derived} has no pins — parser broke"
        drift = {
            name: (version, root[name])
            for name, version in service.items()
            if name in root and root[name] != version
        }
        assert drift == {}, (
            f"{derived} must copy the root lockfile's versions "
            f"verbatim; these differ (service, root): {drift}"
        )

    @pytest.mark.parametrize("derived", _DERIVED_LOCKFILES)
    def test_derived_introduces_no_package_the_root_lockfile_lacks(self, derived):
        """A service-only package would be installed at a version CI never ran."""
        root = _pins(_ROOT / "requirements.txt")
        service = _pins(_ROOT / derived)
        orphans = sorted(set(service) - set(root))
        assert orphans == [], (
            f"these are pinned for {derived} but absent from the root "
            f"lockfile, so nothing tests them: {orphans}"
        )

    def test_every_requirement_is_an_exact_pin(self):
        """The 2026-08-05 defect's shape: a requirement present but unpinned."""
        for name in ("requirements.txt", *_DERIVED_LOCKFILES):
            loose = _unpinned_requirement_lines(_ROOT / name)
            assert loose == [], f"{name} has non-`==` requirement lines: {loose}"

    def test_the_parser_can_actually_see_drift(self):
        """The control: these assertions rest on `_pins` reading real content.

        A parser that silently returned `{}` would make every test above pass
        on any file at all, which is the failure mode this whole class exists
        to prevent elsewhere.
        """
        root = _pins(_ROOT / "requirements.txt")
        service = _pins(_ROOT / "backtester" / "requirements.txt")
        assert len(root) > 50 and len(service) > 20
        assert set(service) < set(root), "service set should be a strict subset"
        # A doctored pin must register as drift.
        doctored = dict(service)
        first = sorted(doctored)[0]
        doctored[first] = "0.0.0-not-a-real-version"
        assert any(
            root.get(name) != version
            for name, version in doctored.items()
            if name in root
        )


# ---------------------------------------------------------------------------
# The deployed backtester image ships the currency-resolution layers
# ---------------------------------------------------------------------------


class TestBacktesterImageShipsCurrencyLayers:
    """`engine.quotes` needs its two maps, and the image did not ship them.

    Until 2026-08-07 `backtester/Dockerfile` copied the OHLCV store, portfolios,
    strategies and universes, but neither `data/ticker_currencies.json` (layer
    1, the hand-maintained override — three entries) nor `data/tickers.json`
    (layer 2, the vendor-captured registry — 1,019 tickers carrying a
    currency). Missing, `engine.quotes` falls through to the suffix heuristic,
    which reads every `.L` ticker as pence-quoted sterling — so the image
    resolved `PHAG.L` as GBP at a 0.01 vendor scale when it actually quotes in
    USD. That is a 100x unit error on the very yfinance-fallback path W7.1
    built `_normalise_vendor_frame` to protect.

    **Layer 2 is the one doing the work here**, which is worth stating because
    it is the less obvious of the two: the override map is tiny and holds no
    `.L` ticker at all. The registry is the file that would be missed.
    """

    @staticmethod
    def _dockerfile() -> str:
        return (_ROOT / "backtester" / "Dockerfile").read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "asset", ["data/ticker_currencies.json", "data/tickers.json"]
    )
    def test_the_image_copies_both_currency_layers(self, asset):
        copied = [
            line.strip()
            for line in self._dockerfile().splitlines()
            if line.strip().startswith("COPY") and asset in line
        ]
        assert copied, (
            f"{asset} is not COPYied into the backtester image; engine.quotes "
            "would fall through to the suffix heuristic in production"
        )

    def test_the_files_the_dockerfile_promises_actually_exist(self):
        """A COPY of a missing path fails the build, not the test — catch it here."""
        for asset in ("data/ticker_currencies.json", "data/tickers.json"):
            assert (_ROOT / asset).exists(), f"{asset} is missing from the repo"

    def test_dropping_the_override_map_really_does_corrupt_a_ticker(
        self, tmp_path, monkeypatch
    ):
        """The control: without this, the two assertions above are cargo cult.

        Reproduces the deployed image's data root — store present, ticker maps
        absent — and shows `PHAG.L` flipping to GBP/0.01. If a future change to
        the resolution order makes the maps redundant, this test fails and the
        Dockerfile assertions above can be reconsidered on evidence.
        """
        import json as _json

        (tmp_path / "data" / "market" / "ohlcv").mkdir(parents=True)
        (tmp_path / "roster.yaml").write_text(
            (_ROOT / "roster.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        monkeypatch.setenv("MIDAS_DATA_DIR", str(tmp_path))

        from engine import config as engine_config
        from engine import quotes

        engine_config.get_config.cache_clear()
        for cached in ("_override_map", "_registry_currencies"):
            fn = getattr(quotes, cached, None)
            if fn is not None and hasattr(fn, "cache_clear"):
                fn.cache_clear()

        try:
            degraded_currency = quotes.ticker_currency("PHAG.L")
            degraded_scale = quotes.vendor_unit_scale("PHAG.L")
        finally:
            engine_config.get_config.cache_clear()
            for cached in ("_override_map", "_registry_currencies"):
                fn = getattr(quotes, cached, None)
                if fn is not None and hasattr(fn, "cache_clear"):
                    fn.cache_clear()

        # Layer 2, the vendor registry, is what answers for PHAG.L — NOT the
        # override map, which holds only three hand-written entries. Checked
        # rather than assumed: an earlier draft of this test asserted the
        # override map and was wrong, which is the whole reason the assertion
        # names its source.
        registry = _json.loads(
            (_ROOT / "data" / "tickers.json").read_text(encoding="utf-8")
        )
        assert registry.get("PHAG.L", {}).get("currency") == "USD", (
            "fixture assumption broke: PHAG.L is no longer a USD registry entry"
        )
        assert (degraded_currency, degraded_scale) == ("GBP", 0.01), (
            "expected the map-less root to mis-resolve PHAG.L; got "
            f"{degraded_currency}/{degraded_scale}"
        )


class TestAdjudicationLedgerIsCommitted:
    """The corporate-action ledger must leave the runner.

    It is what stops `apply_split` running twice on one action, and it is the
    disclosure artifact for a path that can move a published share count. A
    ledger that exists only for the life of a job would let every run believe
    nothing had ever been adjudicated — the double-apply the file exists to
    prevent, arriving nightly.
    """

    LEDGER = "data/market/corporate_actions.jsonl"
    WORKFLOWS = ["fetch-ohlcv.yml", "resweep-held-tickers.yml"]

    def _push_paths(self, workflow: str) -> str:
        spec = yaml.safe_load(
            (REPO_ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        )
        steps = [s for job in spec["jobs"].values() for s in job["steps"]]
        pushes = [
            s
            for s in steps
            if str(s.get("uses", "")).endswith("actions/push-with-retry")
        ]
        assert pushes, f"{workflow} has no push-with-retry step"
        return " ".join(str(s["with"]["paths"]) for s in pushes)

    @pytest.mark.parametrize("workflow", WORKFLOWS)
    def test_whatever_can_apply_a_split_also_stages_the_holdings(self, workflow):
        """Both split paths mutate data/portfolios/ — so both must commit it.

        `_apply_split_to_holders` writes portfolio.json. Adjudication can only
        fire from the nightly fetch (quarantine is off on a resweep), and that
        workflow staged the ledger but NOT the holdings: the share correction
        would die with the runner while the ledger row saying it was applied
        landed on main. The next run finds the key in `already`, skips the
        apply, and the book keeps half its shares against a post-split price,
        permanently. Same class as METHODOLOGY #lost-fill-2026-05-21, where a
        `git add` pathspec omitted data/portfolios.

        Second-order: unstaged worktree changes also break the retry, since
        `git pull --rebase` refuses to run with them and the step is under
        `set -euo pipefail`.
        """
        assert "data/portfolios/" in self._push_paths(workflow), (
            f"{workflow} can call apply_split but does not stage data/portfolios/"
        )

    @pytest.mark.parametrize("workflow", WORKFLOWS)
    def test_the_ledger_is_in_the_push_paths(self, workflow):
        spec = yaml.safe_load(
            (REPO_ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        )
        steps = [s for job in spec["jobs"].values() for s in job["steps"]]
        pushes = [
            s
            for s in steps
            if str(s.get("uses", "")).endswith("actions/push-with-retry")
        ]
        assert pushes, f"{workflow} has no push-with-retry step"
        paths = " ".join(str(s["with"]["paths"]) for s in pushes)
        assert self.LEDGER in paths, (
            f"{workflow} does not stage {self.LEDGER}; an adjudication would be "
            "forgotten and re-applied on the next run"
        )


class TestCryptoWatcherIsDispatchOnly:
    """The hourly crypto cron must stay retired (2026-08-18).

    The whole quota fix rests on it: at 23 runs/day, 6 of the first 8 runs
    measured 58-71s — astride GitHub's round-up-to-the-minute billing edge —
    which projected ~1,150-1,200 of the account's 2,000 monthly minutes to do
    a few minutes of work. Hourly evaluation moved to the Cloudflare Worker at
    `workers/trigger-gate/`, which dispatches this workflow only on a hit.

    Asserted in BOTH directions, like the fetch-ohlcv schedule guard above:
    "no cron" alone would pass a workflow that lost its `workflow_dispatch`
    trigger too and became unreachable — which is not a cheaper watcher, it is
    no watcher, and the Worker's dispatch POST would 404 with nothing on the
    GitHub side to notice.
    """

    WORKFLOW = REPO_ROOT / ".github" / "workflows" / "check-triggers-crypto.yml"

    def _triggers(self) -> dict:
        spec = yaml.safe_load(self.WORKFLOW.read_text(encoding="utf-8"))
        # PyYAML resolves the bare key `on` to boolean True (YAML 1.1).
        return spec[True] if True in spec else spec["on"]

    def test_no_schedule_trigger(self):
        assert "schedule" not in self._triggers(), (
            "check-triggers-crypto has a cron again; hourly evaluation belongs "
            "to workers/trigger-gate/, which dispatches this workflow on a hit"
        )

    def test_workflow_dispatch_is_the_way_in(self):
        assert "workflow_dispatch" in self._triggers(), (
            "the Worker gate fires this workflow via workflow_dispatch; without "
            "that trigger the dispatch POST 404s and crypto conditionals "
            "silently degrade to the daily 13:00 sweep"
        )


class TestTestsGateCoversEverySuite:
    """`gate`'s EXPECTED list must name every suite in tests.yml.

    The gate exists because Actions cannot tell "passed" from "never ran": a
    job excluded by `if:` reports `skipped`, one dropped from `needs:` reports
    nothing, and `jq 'all(.[]; .result=="success")'` over an empty set returns
    true — so the obvious aggregating job reports success on zero coverage.
    Naming the expected checks up front is the fix.

    But the naming was itself a manual discipline: CLAUDE.md said "add a suite
    here and you must add its job id to EXPECTED", and nothing enforced it. A
    suite added without its EXPECTED entry is guarded by the same "green by
    omission" hole the gate was built to close, one level up. This makes the
    mandate mechanical.
    """

    WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"

    def _spec(self) -> dict:
        return yaml.safe_load(self.WORKFLOW.read_text(encoding="utf-8"))

    def _gate_expected(self) -> set[str]:
        gate = self._spec()["jobs"]["gate"]
        envs = [
            s["env"]["EXPECTED"]
            for s in gate["steps"]
            if "EXPECTED" in s.get("env", {})
        ]
        assert len(envs) == 1, "gate does not declare exactly one EXPECTED list"
        parsed = json.loads(envs[0])
        assert parsed, "EXPECTED is empty — the gate would pass on zero coverage"
        return set(parsed)

    def test_expected_names_every_other_job(self):
        spec = self._spec()
        suites = set(spec["jobs"]) - {"gate"}
        assert self._gate_expected() == suites, (
            "tests.yml's gate EXPECTED list disagrees with the jobs that exist; "
            "a suite missing from it is green by omission, which is the exact "
            "hole the gate was built to close"
        )

    def test_gate_needs_every_job_it_expects(self):
        """EXPECTED without `needs` is worse than useless: the gate would race
        the suites and read their results before they finish."""
        needs = set(self._spec()["jobs"]["gate"].get("needs") or [])
        assert needs == self._gate_expected(), (
            f"gate needs {sorted(needs)} but expects {sorted(self._gate_expected())}"
        )

    def test_the_gate_always_runs(self):
        """`if: always()` — a gate skipped because a suite failed reports nothing."""
        assert self._spec()["jobs"]["gate"].get("if") == "always()"


# --------------------------------------------------------------------------
# auto-merge-session takes the watcher's `triggers/*` fallback branches
# --------------------------------------------------------------------------

AUTO_MERGE = REPO_ROOT / ".github" / "workflows" / "auto-merge-session.yml"


def _auto_merge_spec() -> dict:
    return yaml.safe_load(AUTO_MERGE.read_text(encoding="utf-8"))


def _auto_merge_step(name: str) -> dict:
    steps = [
        s for s in _auto_merge_spec()["jobs"]["merge"]["steps"] if s.get("name") == name
    ]
    assert len(steps) == 1, f"expected exactly one step named {name!r}"
    return steps[0]


def _git_env(tmp_path: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }


def _watcher_branch_repo(
    tmp_path: Path, commits: list[tuple[str, dict[str, str]]]
) -> tuple[Path, str]:
    """A clone of a bare `main` with a `triggers/…` branch on top.

    ``commits`` is a list of (subject, {path: content}) applied on the branch
    after main's seed commit. Returns the clone and the branch tip sha; the
    scope script is run in the clone with HEAD_SHA set to that tip, exactly
    as the workflow runs it after `actions/checkout`.
    """
    env = _git_env(tmp_path)
    bare = tmp_path / "bare.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True, env=env
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True, env=env)
    (seed / "data" / "orders" / "pending").mkdir(parents=True)
    (seed / "data" / "orders" / "pending" / "ord_1.json").write_text("{}\n")
    (seed / "engine").mkdir()
    (seed / "engine" / "x.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=seed, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=seed, check=True, env=env)
    subprocess.run(
        ["git", "push", "-q", "origin", "HEAD:main"], cwd=seed, check=True, env=env
    )

    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(bare), str(repo)], check=True, env=env)
    subprocess.run(
        ["git", "checkout", "-q", "-b", "triggers/2026-09-05-1"],
        cwd=repo,
        check=True,
        env=env,
    )
    for subject, files in commits:
        for rel, content in files.items():
            path = repo / rel
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", subject],
            cwd=repo,
            check=True,
            env=env,
        )
    tip = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()
    return repo, tip


def _run_scope_check(
    repo: Path, tip: str, tmp_path: Path
) -> subprocess.CompletedProcess:
    script = _auto_merge_step("Verify watcher fallback branch scope")["run"]
    env = {
        **_git_env(tmp_path),
        "HEAD_SHA": tip,
        "GITHUB_OUTPUT": str(tmp_path / "out"),
    }
    return subprocess.run(
        ["bash", "-c", script], cwd=repo, env=env, capture_output=True, text=True
    )


def _run_inspect(
    repo: Path, tip: str, tmp_path: Path, ref: str
) -> tuple[subprocess.CompletedProcess, dict[str, str]]:
    """Run the classifier step and read back what it wrote to $GITHUB_OUTPUT."""
    script = _auto_merge_step("Inspect pushed commit")["run"]
    out = tmp_path / "inspect-out"
    out.write_text("")
    env = {
        **_git_env(tmp_path),
        "HEAD_SHA": tip,
        "GITHUB_REF": ref,
        "GITHUB_OUTPUT": str(out),
    }
    result = subprocess.run(
        ["bash", "-c", script], cwd=repo, env=env, capture_output=True, text=True
    )
    outputs = dict(
        line.split("=", 1) for line in out.read_text().splitlines() if "=" in line
    )
    return result, outputs


A_FILL = {
    "data/orders/pending/ord_1.json": None,
    "data/orders/inbox/2026-09-05.jsonl": '{"order_id":"ord_1","status":"filled"}\n',
    "data/portfolios/satoshi/portfolio.json": "{}\n",
}


class TestAutoMergeTakesWatcherFallbackBranches:
    """Regression: 2026-08-24..09-04 — a refused watcher push had nowhere to go.

    The watcher now pushes a refused commit to `triggers/<date>-<run id>` and
    this workflow merges it. What is pinned here is the contract between the
    two sides, and the rules a watcher branch must pass — run for real against
    fixture branches, so the shell is what gets tested, not a reading of it.
    """

    def test_the_workflow_triggers_on_the_branches_the_watcher_pushes(self):
        from scripts import check_triggers

        spec = _auto_merge_spec()
        on = spec[True] if True in spec else spec["on"]
        branches = on["push"]["branches"]
        assert "triggers/**" in branches, branches
        assert "claude/**" in branches, "the session half must keep working"
        # The two sides name the same prefix: `triggers/**` is what the
        # workflow listens for, `triggers/` is what the watcher pushes under.
        assert check_triggers.FALLBACK_BRANCH_PREFIX == "triggers/"
        assert check_triggers.fallback_branch_name(
            __import__("datetime").date(2026, 9, 5)
        ).startswith("triggers/2026-09-05-")

    def test_the_session_rules_never_run_for_a_watcher_branch(self):
        """A watcher commit publishes no journals, posts or baselines; the
        session artifact rules would refuse every one of them. They are
        gated on the session kind, and only on it."""
        for name in (
            "Verify session-integrity rules",
            "Reject stale sessions",
            "Merge sandbox session into main",
        ):
            assert (
                _auto_merge_step(name).get("if")
                == "steps.inspect.outputs.kind == 'session'"
            ), name

    def test_the_watcher_steps_run_only_for_a_watcher_branch(self):
        for name in (
            "Verify watcher fallback branch scope",
            "Merge watcher fallback branch into main",
        ):
            cond = _auto_merge_step(name).get("if") or ""
            assert "steps.inspect.outputs.kind == 'triggers'" in cond, name
            assert "already_merged != 'true'" in cond, name

    def test_the_branch_is_deleted_for_both_kinds(self):
        step = _auto_merge_step("Delete merged branch")
        cond = step.get("if") or ""
        assert "kind == 'session'" in cond and "kind == 'triggers'" in cond
        assert "--delete" in step["run"]

    def test_the_watcher_merge_never_rewrites_history(self):
        """Fast-forward or merge commit; the fills carry `executed_sha`
        provenance that a rebase or a force would break."""
        script = _auto_merge_step("Merge watcher fallback branch into main")["run"]
        assert "rebase" not in script
        assert "--force" not in script
        assert "reset --hard" not in script
        assert "git merge" in script
        # A conflict is a human's problem, not a retry's.
        assert "merge --abort" in script and "exit 1" in script

    def test_a_failed_watcher_merge_files_its_own_issue(self):
        """Distinct title, gated on the kind: a green SESSION merge must not
        close the watcher's issue while the watcher branch is still unmerged.
        Title uniqueness across workflows is asserted by
        test_every_alerting_workflow_has_its_own_issue_identity."""
        step = _auto_merge_step("Report watcher-merge outcome")
        assert step["uses"] == "./.github/actions/failure-issue"
        cond = step["if"]
        assert cond.startswith("always()") and "kind == 'triggers'" in cond
        assert "watcher" in step["with"]["title"]
        assert _auto_merge_spec()["permissions"]["issues"] == "write"
        # The body must not promise something that is no longer true: the fill
        # on a branch is not lost, and the watcher refuses to run behind it.
        body = step["with"]["body"]
        assert "NOT lost" in body and "refuses" in body

    def test_the_allowed_paths_match_the_watcher(self):
        """One list, two copies: the scope regex in the workflow and
        WATCHER_PATHS in scripts/check_triggers.py must agree."""
        from scripts import check_triggers

        script = _auto_merge_step("Verify watcher fallback branch scope")["run"]
        m = re.search(r"grep -Ev '\^data/\(([a-z_|]+)\)/'", script)
        assert m, "could not find the allow-list regex in the scope step"
        in_workflow = {f"data/{p}/" for p in m.group(1).split("|")}
        assert in_workflow == set(check_triggers.WATCHER_PATHS)

    # --- the scope script, executed ---------------------------------------

    def test_the_scope_check_accepts_a_real_watcher_branch(self, tmp_path):
        repo, tip = _watcher_branch_repo(
            tmp_path,
            [
                ("chore(triggers): execute ord_1 2026-09-05", A_FILL),
                (
                    "chore(triggers): execute fired/expired conditions 2026-09-05",
                    {"data/leaderboard/current.json": "{}\n"},
                ),
            ],
        )
        result = _run_scope_check(repo, tip, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_the_scope_check_refuses_a_file_outside_the_watcher_paths(self, tmp_path):
        repo, tip = _watcher_branch_repo(
            tmp_path,
            [
                (
                    "chore(triggers): execute ord_1 2026-09-05",
                    {**A_FILL, "engine/x.py": "x = 2\n"},
                )
            ],
        )
        result = _run_scope_check(repo, tip, tmp_path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "engine/x.py" in result.stdout

    def test_the_scope_check_refuses_a_commit_that_is_not_the_watchers(self, tmp_path):
        """Every commit on the branch, not just the tip: a session-shaped
        commit hiding under a watcher tip would otherwise ride in."""
        repo, tip = _watcher_branch_repo(
            tmp_path,
            [
                (
                    "chore: weekday session 2026-09-05",
                    {"data/portfolios/satoshi/snapshots.json": "[]\n"},
                ),
                ("chore(triggers): execute ord_1 2026-09-05", A_FILL),
            ],
        )
        result = _run_scope_check(repo, tip, tmp_path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "not a watcher commit" in result.stdout

    def test_the_scope_check_refuses_zero_coverage(self, tmp_path):
        """ "Every changed file is allowed" over NO files is vacuously true —
        the same hole tests.yml's gate closes with a named EXPECTED list.
        Both empty shapes are refused: no commits over main, and commits
        that change nothing."""
        repo, tip = _watcher_branch_repo(tmp_path, [])
        result = _run_scope_check(repo, tip, tmp_path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "zero coverage" in result.stdout

        repo, tip = _watcher_branch_repo(
            tmp_path / "empty-commit",
            [("chore(triggers): execute ord_1 2026-09-05", {})],
        )
        result = _run_scope_check(repo, tip, tmp_path / "empty-commit")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "changes no files" in result.stdout

    # --- the classifier, executed -----------------------------------------

    def test_a_triggers_branch_is_a_watcher_branch_whatever_its_tip_says(
        self, tmp_path
    ):
        """Round-4 review, 2026-09-05: `kind=none` was the one classification
        with no consumer at all.

        A `triggers/*` branch whose tip subject was not `chore(triggers): `
        classified as `none`, and every downstream step is gated on the kind —
        so the run went green having verified nothing, merged nothing, deleted
        nothing and reported nothing, while the branch sat on origin making
        `scripts/check_triggers.py` refuse to evaluate (no fires, no expiries,
        desk-wide) until a human deleted it. And that tip is reachable by
        invitation: the stale-refusal issue tells the reader to resolve the
        branch by hand, and an amend, a hand-carried fill or a revert all
        leave a subject the prefix regex does not match. The branch name now
        decides, so the scope step below refuses that commit by name, the run
        goes red, and the watcher-merge issue is filed.
        """
        repo, tip = _watcher_branch_repo(
            tmp_path,
            [("fix: carry ord_1's fill over by hand", A_FILL)],
        )
        result, outputs = _run_inspect(
            repo, tip, tmp_path, "refs/heads/triggers/2026-09-05-1"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert outputs["kind"] == "triggers", result.stdout

        # …and the consequence: the scope step is what refuses it, loudly.
        scope = _run_scope_check(repo, tip, tmp_path)
        assert scope.returncode == 1, scope.stdout + scope.stderr
        assert "not a watcher commit" in scope.stdout

    def test_a_watcher_tip_on_a_triggers_branch_still_classifies(self, tmp_path):
        """The ordinary case the fix must not change."""
        repo, tip = _watcher_branch_repo(
            tmp_path, [("chore(triggers): execute ord_1 2026-09-05", A_FILL)]
        )
        _, outputs = _run_inspect(
            repo, tip, tmp_path, "refs/heads/triggers/2026-09-05-1"
        )
        assert outputs["kind"] == "triggers"
        assert outputs["already_merged"] == "false"

    def test_the_classifier_can_still_answer_none(self, tmp_path):
        """The control. Without it, `kind=triggers` for everything would pass
        the test above forever. A `claude/**` branch carrying neither a
        session nor a watcher commit keeps `kind=none` deliberately: a sandbox
        branch is pushed for reasons unrelated to this workflow, it blocks
        nothing on origin, and session-watchdog is what notices a session that
        never reached main.
        """
        repo, tip = _watcher_branch_repo(
            tmp_path, [("fix: something a sandbox pushed", A_FILL)]
        )
        _, outputs = _run_inspect(
            repo, tip, tmp_path, "refs/heads/claude/dreamy-lovelace-6t8ifh"
        )
        assert outputs["kind"] == "none"

    def test_a_session_tip_on_a_sandbox_branch_still_classifies(self, tmp_path):
        """The session half is untouched by the branch-name rule."""
        repo, tip = _watcher_branch_repo(
            tmp_path,
            [
                (
                    "chore: weekday session 2026-09-05",
                    {"data/portfolios/satoshi/snapshots.json": "[]\n"},
                )
            ],
        )
        _, outputs = _run_inspect(
            repo, tip, tmp_path, "refs/heads/claude/dreamy-lovelace-6t8ifh"
        )
        assert outputs["kind"] == "session"


# --------------------------------------------------------------------------
# tests.yml's gate, executed: it must not pass on zero coverage
# --------------------------------------------------------------------------


def _gate_script() -> str:
    spec = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text()
    )
    steps = [s for s in spec["jobs"]["gate"]["steps"] if "EXPECTED" in s.get("env", {})]
    assert len(steps) == 1
    return steps[0]["run"]


def _run_gate(tmp_path: Path, expected: str, needs: str) -> int:
    return subprocess.run(
        ["bash", "-c", _gate_script()],
        env={**_git_env(tmp_path), "EXPECTED": expected, "NEEDS": needs},
        capture_output=True,
        text=True,
    ).returncode


class TestTestsGateCannotPassOnZeroCoverage:
    """`TestTestsGateCoversEverySuite` reads the YAML; this runs the shell.

    The gate's whole reason to exist is that `jq 'all(.[]; …)'` over an empty
    set returns true. A named list is only a fix if the script that consumes
    it actually refuses the empty shapes — pinned by running it.
    """

    def test_every_expected_suite_green_passes(self, tmp_path):
        """The control, or the three refusals below would pass an `exit 1`."""
        needs = json.dumps({"a": {"result": "success"}, "b": {"result": "success"}})
        assert _run_gate(tmp_path, '["a","b"]', needs) == 0

    def test_an_empty_expected_list_is_refused(self, tmp_path):
        needs = json.dumps({"a": {"result": "success"}})
        assert _run_gate(tmp_path, "[]", needs) == 1

    def test_an_expected_suite_that_never_ran_is_refused(self, tmp_path):
        """Absent from `needs` entirely — what a dropped `needs:` entry looks like."""
        needs = json.dumps({"a": {"result": "success"}})
        assert _run_gate(tmp_path, '["a","b"]', needs) == 1

    def test_a_skipped_expected_suite_is_refused(self, tmp_path):
        """`skipped` is what an `if:`-excluded job reports, and it is not success."""
        needs = json.dumps({"a": {"result": "success"}, "b": {"result": "skipped"}})
        assert _run_gate(tmp_path, '["a","b"]', needs) == 1


# --------------------------------------------------------------------------
# merge-fallback-branches — the watcher has to DISPATCH the merge
# --------------------------------------------------------------------------

MERGE_FALLBACK_ACTION = (
    REPO_ROOT / ".github" / "actions" / "merge-fallback-branches" / "action.yml"
)
WATCHER_WORKFLOWS = ["check-triggers.yml", "check-triggers-crypto.yml"]


def _merge_fallback_script() -> str:
    action = yaml.safe_load(MERGE_FALLBACK_ACTION.read_text())
    steps = action["runs"]["steps"]
    assert len(steps) == 1
    return steps[0]["run"]


def _run_merge_fallback(
    tmp_path: Path,
    branches: list[str],
    *,
    gh_rc: int = 0,
    wait_seconds: int = 0,
    delete_after: list[str] | None = None,
) -> tuple[int, list[str], str]:
    """Run the action's real script in a clone whose origin holds ``branches``.

    `gh` is stubbed; returns the exit code, the gh invocations (the observable
    the watcher depends on) and the step's stdout.

    ``delete_after`` names branches the stubbed `gh` deletes from the bare on
    its way out — the merge landing, modelled. That is what the wait loop is
    watching for.
    """
    env = _git_env(tmp_path)
    bare = tmp_path / "bare.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True, env=env
    )
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(bare), str(repo)], check=True, env=env)
    (repo / "x").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True, env=env)
    subprocess.run(
        ["git", "push", "-q", "origin", "HEAD:main"], cwd=repo, check=True, env=env
    )
    for b in branches:
        subprocess.run(
            ["git", "push", "-q", "origin", f"HEAD:refs/heads/{b}"],
            cwd=repo,
            check=True,
            env=env,
        )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "gh-calls.txt"
    gh = bin_dir / "gh"
    delete_lines = "".join(
        f'git --git-dir="{bare}" update-ref -d refs/heads/{b}\n'
        for b in (delete_after or [])
    )
    gh.write_text(f'#!/bin/bash\necho "$*" >> "{calls}"\n{delete_lines}exit {gh_rc}\n')
    gh.chmod(0o755)

    result = subprocess.run(
        ["bash", "-c", _merge_fallback_script()],
        cwd=repo,
        env={
            **env,
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "GH_TOKEN": "stub",
            "WORKFLOW": "auto-merge-session.yml",
            "REPO": "w2ur/midas",
            "WAIT_SECONDS": str(wait_seconds),
        },
        capture_output=True,
        text=True,
    )
    return (
        result.returncode,
        (calls.read_text().splitlines() if calls.exists() else []),
        result.stdout + result.stderr,
    )


class TestWatcherDispatchesTheMerge:
    """A GITHUB_TOKEN push fires no `on: push` workflow.

    That is the documented rule, with workflow_dispatch and repository_dispatch
    as the only exceptions — and it is why the session's `claude/**` fallback
    works (the sandbox pushes with its own credential) while a watcher push
    to `triggers/*` would sit there forever. So the watcher workflows dispatch
    the merge themselves, for EVERY unmerged fallback branch, on every run.
    """

    def test_both_watchers_dispatch_after_the_run_whatever_happened(self):
        for name in WATCHER_WORKFLOWS:
            spec = yaml.safe_load(
                (REPO_ROOT / ".github" / "workflows" / name).read_text()
            )
            steps = spec["jobs"]["check"]["steps"]
            uses = [s.get("uses") for s in steps]
            names = [s.get("name") for s in steps]
            users = [
                i
                for i, s in enumerate(steps)
                if s.get("uses") == "./.github/actions/merge-fallback-branches"
            ]
            # TWICE, before and after the watcher (round-2 review,
            # 2026-09-05). Dispatching only afterwards meant a branch left
            # from a deferred or failed merge cost this run its whole
            # evaluation — check_triggers.py refuses while one exists, and
            # only then was the merge asked for.
            assert len(users) == 2, (
                f"{name} does not dispatch the fallback merge before AND after the run"
            )
            lead, trail = users
            run_step = next(
                i for i, n in enumerate(names) if n and n.startswith("Run watcher")
            )
            assert lead < run_step < trail, name
            # The leading one waits for the merge to land, or it has bought
            # nothing: the watcher would refuse behind the same branch.
            assert int(steps[lead]["with"]["wait-seconds"]) > 0, name
            # ...and the checkout is re-anchored between them, or the watcher
            # refuses on `origin/main moved since this checkout` instead.
            anchor = next(
                i
                for i, n in enumerate(names)
                if n == "Re-anchor the checkout on main's current tip"
            )
            assert lead < anchor < run_step, name
            # `if: always()` on the trailing one: the run that refused to
            # evaluate behind an unmerged branch is the run that must try to
            # get it merged.
            assert steps[trail].get("if") == "always()", name
            # ...and it must run BEFORE the reporter, so a failed dispatch is
            # part of the outcome that gets reported.
            assert trail < uses.index("./.github/actions/failure-issue"), name
            # `gh workflow run` needs actions: write, or it 403s silently.
            assert spec["permissions"]["actions"] == "write", f"{name} cannot dispatch"

    def test_the_merge_workflow_can_be_dispatched(self):
        spec = _auto_merge_spec()
        on = spec[True] if True in spec else spec["on"]
        assert "workflow_dispatch" in on, (
            "auto-merge-session has no workflow_dispatch trigger; a GITHUB_TOKEN "
            "push to triggers/* fires nothing, so the watcher's branch would never merge"
        )

    def test_a_clean_origin_dispatches_nothing_and_passes(self, tmp_path):
        rc, calls, _out = _run_merge_fallback(tmp_path, [])
        assert rc == 0 and calls == []

    def test_every_fallback_branch_is_dispatched(self, tmp_path):
        """All of them, not the newest: a lost dispatch from an earlier run is
        exactly what the next run is meant to repair."""
        rc, calls, _out = _run_merge_fallback(
            tmp_path,
            ["triggers/2026-09-04-1", "triggers/2026-09-05-2", "claude/not-ours"],
        )
        assert rc == 0
        assert sorted(calls) == [
            "workflow run auto-merge-session.yml --repo w2ur/midas --ref triggers/2026-09-04-1",
            "workflow run auto-merge-session.yml --repo w2ur/midas --ref triggers/2026-09-05-2",
        ]

    def test_a_failed_dispatch_fails_the_step(self, tmp_path):
        """The branch is then unmerged with nothing scheduled to merge it;
        the calling workflow's failure-issue is the only way that reaches a human."""
        rc, calls, _out = _run_merge_fallback(
            tmp_path, ["triggers/2026-09-05-2"], gh_rc=1
        )
        assert rc == 1 and len(calls) == 1



# --------------------------------------------------------------------------
# The retry sweep — a waiting fallback branch gets another dispatch BEFORE
# tonight's session (round-5 review, 2026-09-05)
# --------------------------------------------------------------------------

RETRY_SWEEP = "retry-fallback-merges.yml"


def _workflow_spec(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / ".github" / "workflows" / name).read_text())


def _on(spec: dict) -> dict:
    # PyYAML reads a bare `on:` key as the boolean True.
    return spec[True] if True in spec else spec["on"]


def _crons(spec: dict) -> list[tuple[int, int]]:
    """Every declared cron as (hour, minute) UTC."""
    out = []
    for entry in (_on(spec).get("schedule") or []):
        minute, hour = entry["cron"].split()[:2]
        out.append((int(hour), int(minute)))
    return out


def _dispatches_the_merge(spec: dict) -> bool:
    return any(
        step.get("uses") == "./.github/actions/merge-fallback-branches"
        for job in spec["jobs"].values()
        for step in job["steps"]
    )


class TestAWaitingFallbackBranchIsRetriedBeforeTheSession:
    """Regression (round-5 review, 2026-09-05): the only re-dispatcher was a
    watcher run, and none is scheduled between the 13:00 UTC sweep and the
    session.

    Every non-fatal outcome of auto-merge-session — a deferral, a rejected
    push ("main moved after the stale check"), a merge conflict, a lost
    dispatch — ends with "the next run re-dispatches this merge". The callers
    of `.github/actions/merge-fallback-branches` were check-triggers.yml
    (`0 13 * * *`) and check-triggers-crypto.yml (workflow_dispatch only,
    fired by the Cloudflare gate when a crypto trigger is near its level, i.e.
    on most days never). So the next attempt after the 13:00 sweep was 13:00
    TOMORROW — past the merge's hard deadline, tonight's 20:00 session, after
    which the branch is stale by construction: the stale check keys a book as
    one path and every session writes `data/portfolios/<agent>/snapshots.json`
    for every book.

    One transient failure was therefore enough to reproduce the outage this
    whole fallback exists to end: the fill executed at the 13:00 price never
    reaches the published ledger, the agent trades that evening against a book
    that does not hold it, and from the next watcher run the desk is halted —
    fires and expiries alike — until a human deletes the branch and the fill
    is re-executed by hand at a different price.
    """

    def _sweeps(self) -> dict[str, dict]:
        """Every scheduled workflow that re-dispatches the merge and is not
        itself a watcher run (a watcher run's cadence is a money-path decision
        and cannot be moved to serve the merge)."""
        found = {}
        for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
            if path.name in WATCHER_WORKFLOWS:
                continue
            spec = _workflow_spec(path.name)
            if _dispatches_the_merge(spec) and _crons(spec):
                found[path.name] = spec
        return found

    def test_a_scheduled_sweep_exists_outside_the_watcher_workflows(self):
        sweeps = self._sweeps()
        assert sweeps, (
            "nothing but a watcher run re-dispatches auto-merge-session, so a "
            "merge that fails after the 13:00 sweep is not retried until the "
            "next day — past its hard deadline, tonight's session"
        )
        assert RETRY_SWEEP in sweeps, sorted(sweeps)

    def test_every_retry_lands_between_the_watcher_sweep_and_the_session(self):
        from scripts import check_triggers

        watcher = min(_crons(_workflow_spec("check-triggers.yml")))
        session = (check_triggers.SESSION_START.hour, check_triggers.SESSION_START.minute)
        for name, spec in self._sweeps().items():
            for cron in _crons(spec):
                assert watcher < cron < session, (
                    f"{name} retries at {cron[0]:02d}:{cron[1]:02d} UTC, outside "
                    f"the window the merge can still land in "
                    f"({watcher[0]:02d}:{watcher[1]:02d}..{session[0]:02d}:"
                    f"{session[1]:02d}); past the session the branch is stale by "
                    "construction and the retry buys nothing"
                )

    def test_the_retry_is_not_a_single_point(self):
        """GitHub's scheduler typically lands 40 min to 2 h 45 late with a tail
        past 5 h (measured across this repo). One retry would be one late run —
        or one lost dispatch — away from the trap it exists to prevent."""
        crons = _crons(_workflow_spec(RETRY_SWEEP))
        assert len(set(crons)) >= 2, crons

    def test_the_sweep_can_actually_dispatch_and_says_when_it_cannot(self):
        spec = _workflow_spec(RETRY_SWEEP)
        # `gh workflow run` 403s silently without this.
        assert spec["permissions"]["actions"] == "write"
        # It moves nothing on main itself — it dispatches, and every check
        # re-runs inside auto-merge-session against whatever main is by then.
        assert spec["permissions"]["contents"] == "read"
        steps = [s for job in spec["jobs"].values() for s in job["steps"]]
        uses = [s.get("uses") for s in steps]
        assert "./.github/actions/merge-fallback-branches" in uses
        # A red X on a sweep nobody watches is the failure this repo has paid
        # for twice; the reporter must run whatever the dispatch did.
        reporter = next(
            s for s in steps if s.get("uses") == "./.github/actions/failure-issue"
        )
        assert reporter.get("if") == "always()"
        assert uses.index("./.github/actions/merge-fallback-branches") < uses.index(
            "./.github/actions/failure-issue"
        )


# --------------------------------------------------------------------------
# Task A3 (2026-09-05) — the failure-issue must say what fired
# --------------------------------------------------------------------------
#
# scripts/check_triggers.py's write_run_report() appends a markdown table to
# $GITHUB_STEP_SUMMARY. These pin the two things that had to change for a
# reader of the issue to see it: failure-issue accepting an optional
# `details` input without changing behaviour for anyone who does not pass it,
# and both watcher workflows actually reading the summary back and wiring it
# in — a body-writer with no caller is the same "check nobody reads" failure
# this whole file exists to catch.


def _run_failure_issue_capturing_body(
    tmp_path: Path, outcome: str, *, existing: str = "", details: str = ""
) -> str:
    """Like `_run_failure_issue`, but returns the CONTENT of whichever
    `--body-file` `gh` was called with, not just the call sequence — the
    observable that matters for `details`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls = tmp_path / "gh-calls.txt"
    body_capture = tmp_path / "captured-body.txt"
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/bash\n"
        f'echo "$1 $2" >> "{calls}"\n'
        'prev=""\n'
        'for a in "$@"; do\n'
        f'  if [[ "$prev" == "--body-file" ]]; then cp "$a" "{body_capture}"; fi\n'
        '  prev="$a"\n'
        "done\n"
        f'if [[ "$1 $2" == "issue list" ]]; then printf "%s" "{existing}"; fi\n'
        f'if [[ "$1 $2" == "issue create" ]]; then echo "https://example/issues/1"; fi\n'
        "exit 0\n"
    )
    gh.chmod(0o755)

    result = subprocess.run(
        ["bash", "-c", _failure_issue_script()],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "RUNNER_TEMP": str(tmp_path),
            "GH_TOKEN": "stub",
            "TITLE": "some-job: it broke",
            "BODY": "why it matters",
            "DETAILS": details,
            "OUTCOME": outcome,
            "RUN_URL": "https://example/run/1",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return body_capture.read_text() if body_capture.exists() else ""


class TestFailureIssueDetailsInput:
    """The `details` input failure-issue gained for the watcher report."""

    def test_declares_an_optional_details_input_defaulting_to_empty(self):
        action = yaml.safe_load(FAILURE_ISSUE_ACTION.read_text())
        details = action["inputs"].get("details")
        assert details is not None, "failure-issue has no `details` input"
        assert not details.get("required", False), (
            "details must be optional — every EXISTING caller does not pass it"
        )
        assert details.get("default") == "", (
            "a non-empty default would change every OTHER caller's body/comment "
            "without them asking for it"
        )

    def test_details_is_appended_to_a_new_issue_body(self, tmp_path):
        body = _run_failure_issue_capturing_body(
            tmp_path,
            "failure",
            details="| order | commit |\n|---|---|\n| ord_1 | main |",
        )
        assert "why it matters" in body
        assert "| ord_1 | main |" in body

    def test_details_is_appended_to_a_recurrence_comment(self, tmp_path):
        """The recurrence path used an inline `--body`, which can't carry
        arbitrary markdown safely; it is now a body-file too, and DETAILS
        rides along on a SECOND failure exactly as it does on the first."""
        body = _run_failure_issue_capturing_body(
            tmp_path, "failure", existing="42", details="| ord_1 | stranded |"
        )
        assert "Still failing" in body
        assert "| ord_1 | stranded |" in body

    def test_empty_details_reproduces_the_original_body_byte_for_byte(self, tmp_path):
        """The backward-compatibility control: every OTHER caller passes no
        `details`, which resolves to the empty-string default — and that must
        still be exactly the body every existing caller was already getting,
        not merely "still contains BODY somewhere"."""
        body = _run_failure_issue_capturing_body(tmp_path, "failure", details="")
        assert body == (
            "why it matters\n\n---\n\nDetected by [this run](https://example/run/1).\n"
        )


class TestWatcherReportFeedsTheIssue:
    """Both watcher workflows must read the watcher's run report back out of
    `$RUNNER_TEMP` and hand it to failure-issue — the wiring half of A3; the
    writer half is tested against scripts/check_triggers.py directly.

    Regression (round-1 review, 2026-09-05): the reader step used to read
    `$GITHUB_STEP_SUMMARY`, and that file is PER-STEP — GitHub: "unique to
    the current step and changes for each step in a job". So it read its own
    fresh, empty file, wrote `table=`, and every issue went out without the
    table while nothing went red. The test that existed only grepped the
    step's text for the env var's name, which is how a wrong model of the
    runner survived review. `test_the_reader_sees_what_the_writer_wrote` now
    runs the writer and the reader with DIFFERENT step-summary files and one
    shared `$RUNNER_TEMP`, the way the runner does.
    """

    def _steps(self, name: str) -> list[dict]:
        spec = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / name).read_text())
        return spec["jobs"]["check"]["steps"]

    def _reader(self, name: str) -> dict:
        readers = [s for s in self._steps(name) if s.get("id") == "report"]
        assert len(readers) == 1, f"{name} has no single 'report' step"
        return readers[0]

    def test_both_watchers_read_the_runner_temp_report_not_the_step_summary(self):
        from scripts import check_triggers as ct

        for name in WATCHER_WORKFLOWS:
            reader = self._reader(name)
            assert reader.get("if") == "always()", (
                f"{name}: the report step must run even after a failed watcher "
                "— that is precisely the run whose report matters"
            )
            script = reader["run"]
            assert f"$RUNNER_TEMP/{ct.REPORT_MD_FILENAME}" in script, (
                f"{name}: the reader does not read the file the writer writes"
            )
            assert "GITHUB_STEP_SUMMARY" not in script, (
                f"{name}: $GITHUB_STEP_SUMMARY is per-step; a later step reading "
                "it gets an empty file"
            )
            assert "GITHUB_OUTPUT" in script
            assert "<<" in script, (
                f"{name}: a single-line GITHUB_OUTPUT assignment truncates a "
                "multi-line table at the first newline"
            )

    def test_the_reader_sees_what_the_writer_wrote(self, tmp_path):
        """The runner's model, executed: the writer's and the reader's
        `$GITHUB_STEP_SUMMARY` are two different files; `$RUNNER_TEMP` is one
        directory. The reader's output must carry the writer's table."""
        from scripts import check_triggers as ct

        runner_temp = tmp_path / "runner-temp"
        runner_temp.mkdir()
        writer_summary = tmp_path / "step-1-summary.md"
        reader_summary = tmp_path / "step-2-summary.md"
        reader_summary.touch()  # the runner creates it fresh and empty

        entries = [
            {
                "order_id": "ord_probe",
                "agent_id": "satoshi",
                "ticker": "BTC-EUR",
                "action": "SELL",
                "shares": 0.5,
                "op": ">=",
                "level": 90000.0,
                "observed_price": 91000.0,
                "fill_price": 91000.0,
                "notional": 45500.0,
                "kind": "fired",
                "commit": "main",
            }
        ]
        env = {
            ct.RUNNER_TEMP_ENV: str(runner_temp),
            ct.STEP_SUMMARY_ENV: str(writer_summary),
        }
        with pytest.MonkeyPatch.context() as mp:
            for k, v in env.items():
                mp.setenv(k, v)
            mp.delenv(ct.REPORT_PATH_ENV, raising=False)
            ct.write_run_report(
                entries,
                now=__import__("datetime").datetime(
                    2026, 9, 5, 13, 0, tzinfo=__import__("datetime").timezone.utc
                ),
            )
        assert "ord_probe" in writer_summary.read_text()

        for name in WATCHER_WORKFLOWS:
            out = tmp_path / f"{name}.out"
            out.write_text("")
            result = subprocess.run(
                ["bash", "-c", self._reader(name)["run"]],
                env={
                    "PATH": "/usr/bin:/bin",
                    "RUNNER_TEMP": str(runner_temp),
                    "GITHUB_STEP_SUMMARY": str(reader_summary),
                    "GITHUB_OUTPUT": str(out),
                },
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            captured = out.read_text()
            assert captured.startswith("table<<"), captured
            assert "ord_probe" in captured and "satoshi" in captured, (
                f"{name}: the reader did not surface the writer's table"
            )

    def test_an_absent_report_yields_an_empty_table_not_a_failure(self, tmp_path):
        """A blacked-out run or a refused run writes no report; the reader
        must still exit 0 and hand failure-issue an empty `details`."""
        runner_temp = tmp_path / "runner-temp"
        runner_temp.mkdir()
        for name in WATCHER_WORKFLOWS:
            out = tmp_path / f"{name}.out"
            result = subprocess.run(
                ["bash", "-c", self._reader(name)["run"]],
                env={
                    "PATH": "/usr/bin:/bin",
                    "RUNNER_TEMP": str(runner_temp),
                    "GITHUB_OUTPUT": str(out),
                },
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            assert out.read_text().strip() == "table=", name

    def test_both_watchers_upload_the_json_report_as_an_artifact(self):
        """The JSON twin's consumer. `$RUNNER_TEMP` dies with the runner, so
        a report written there and never uploaded is written to nothing —
        which it was on 100% of runs before this step existed."""
        for name in WATCHER_WORKFLOWS:
            uploads = [
                s
                for s in self._steps(name)
                if str(s.get("uses", "")).startswith("actions/upload-artifact@v4")
            ]
            assert len(uploads) == 1, f"{name} does not upload the run report"
            step = uploads[0]
            assert step.get("if") == "always()", name
            with_ = step["with"]
            assert with_["name"] == "watcher-report-${{ github.run_id }}", name
            assert "${{ runner.temp }}/watcher-report." in with_["path"], name
            # A blacked-out or refused run writes nothing; that is not a failure.
            assert with_.get("if-no-files-found") == "ignore", name
            # A re-run attempt reuses the run id.
            assert with_.get("overwrite") is True, name

    def test_both_watchers_pass_the_report_into_failure_issue_as_details(self):
        for name in WATCHER_WORKFLOWS:
            reporters = [
                s
                for s in self._steps(name)
                if s.get("uses") == "./.github/actions/failure-issue"
            ]
            assert len(reporters) == 1, name
            details = reporters[0]["with"].get("details")
            assert details == "${{ steps.report.outputs.table }}", (
                f"{name}: failure-issue is not wired to the watcher report: {details!r}"
            )

    def test_the_report_step_runs_between_the_watcher_and_the_reporter(self):
        """Reading the report before the watcher ran would capture nothing;
        the step must sit strictly between the two named steps."""
        for name in WATCHER_WORKFLOWS:
            names = [s.get("name") for s in self._steps(name)]
            run_idx = next(
                i for i, n in enumerate(names) if n and n.startswith("Run watcher")
            )
            report_idx = names.index("Read watcher run report")
            outcome_idx = names.index("Report outcome")
            assert run_idx < report_idx < outcome_idx, name

    def test_other_alerting_workflows_do_not_pass_details(self):
        """Backward compatibility, asserted rather than assumed: every OTHER
        failure-issue caller must keep the exact prior behaviour, which
        requires none of them to have acquired a `details` key by accident.
        The deliberate callers are the two watchers and auto-merge-session
        (the stale-fill overlap list — see TestAutoMergeRefusesStaleFills)."""
        deliberate = {*WATCHER_WORKFLOWS, "auto-merge-session.yml"}
        for name in ALERTING_WORKFLOWS:
            if name in deliberate:
                continue
            spec = yaml.safe_load(
                (REPO_ROOT / ".github" / "workflows" / name).read_text()
            )
            for job in spec["jobs"].values():
                for step in job["steps"]:
                    if step.get("uses") == "./.github/actions/failure-issue":
                        assert (
                            "details" not in step.get("with", {})
                            or not step["with"]["details"]
                        ), f"{name} now passes `details` — was that deliberate?"


# --------------------------------------------------------------------------
# Round-1 review (2026-09-05) — the watcher merge observes the blackout,
# refuses a stale fill, and resolves the one conflict that is not a ledger
# disagreement
# --------------------------------------------------------------------------


def _advance_main(tmp_path: Path, subject: str, files: dict[str, str | None]) -> str:
    """Land a commit on the bare's `main` from a third clone, the way another
    writer (a session, fetch-ohlcv) does while a fallback branch waits."""
    env = _git_env(tmp_path)
    bare = tmp_path / "bare.git"
    mover = tmp_path / f"mover-{abs(hash(subject)) % 10**6}"
    subprocess.run(["git", "clone", "-q", str(bare), str(mover)], check=True, env=env)
    for rel, content in files.items():
        path = mover / rel
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=mover, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", subject], cwd=mover, check=True, env=env
    )
    subprocess.run(
        ["git", "push", "-q", "origin", "HEAD:main"], cwd=mover, check=True, env=env
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=mover,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


def _bare_main(tmp_path: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=tmp_path / "bare.git",
        check=True,
        capture_output=True,
        text=True,
        env=_git_env(tmp_path),
    ).stdout.strip()


def _run_auto_merge_step(
    name: str, repo: Path, tip: str, tmp_path: Path, **extra_env: str
) -> subprocess.CompletedProcess:
    script = _auto_merge_step(name)["run"]
    env = {
        **_git_env(tmp_path),
        "HEAD_SHA": tip,
        "GITHUB_REF": "refs/heads/triggers/2026-09-05-1",
        "GITHUB_OUTPUT": str(tmp_path / "out"),
        **extra_env,
    }
    return subprocess.run(
        ["bash", "-c", script], cwd=repo, env=env, capture_output=True, text=True
    )


def _outputs(tmp_path: Path) -> str:
    out = tmp_path / "out"
    return out.read_text() if out.exists() else ""


TRIGGER_STEPS_THAT_MOVE_MAIN = (
    "Verify watcher fallback branch scope",
    "Reject stale watcher fallback branches",
    "Merge watcher fallback branch into main",
    "Report watcher-merge outcome",
)


class TestAutoMergeDefersInsideTheSessionWindow:
    """The merge IS the push to main, so it has to stay out of the session's
    way (SESSION_START..BLACKOUT_END — but read, never typed). Before this step
    the watcher's evaluation was blacked out and its publication was not: a
    fire at 19:50 whose merge landed at 20:05 would move the ledger under the
    session anchored at 20:00, and `assert_session_fresh` would discard the
    day's session — journals, posts, snapshots and every session trade.

    Regression (round-2 review, 2026-09-05): the window was `in_blackout`,
    which starts at 19:55 for the watcher's own multi-minute fire path. Those
    five minutes trapped branches — a fire at 19:54 pushed its branch, the
    merge dispatched at ~19:55 deferred, the 20:00 session appended its own
    fills to the same dated inbox file, and the stale step then refused that
    branch forever while every watcher run refused to evaluate behind it. A
    merge is one fetch-merge-push and needs no lead, so it defers from
    SESSION_START: `merge_deferred`, not `in_blackout`."""

    def _step(self) -> dict:
        return _auto_merge_step("Defer inside the session window")

    def test_the_step_exists_and_runs_only_for_watcher_branches(self):
        step = self._step()
        assert step["id"] == "blackout"
        # NOT the session kind: a session merge inside the window IS the
        # session, and deferring it would be the race in the other direction.
        assert step["if"] == "steps.inspect.outputs.kind == 'triggers'"

    def test_every_step_that_moves_main_is_gated_on_the_deferral(self):
        for name in TRIGGER_STEPS_THAT_MOVE_MAIN:
            cond = _auto_merge_step(name).get("if") or ""
            assert "steps.blackout.outputs.deferred != 'true'" in cond, name
        # The delete step too: a deferred branch must survive to be re-dispatched.
        delete = _auto_merge_step("Delete merged branch")["if"]
        assert "steps.blackout.outputs.deferred != 'true'" in delete
        assert "kind == 'session'" in delete, "the session half must keep working"

    def test_the_window_is_read_from_the_watcher_not_typed(self):
        script = self._step()["run"]
        assert "scripts/check_triggers.py" in script
        assert "SESSION_START" in script and "BLACKOUT_END" in script
        # ...and NOT the watcher's own evaluation edge, which is five minutes
        # earlier and is the one that trapped a branch.
        assert "BLACKOUT_START" not in script
        # No hand-typed hour: a `time(HH, MM)` literal here is the second copy
        # that drifted twice already (see BLACKOUT_END's own history).
        assert not re.search(r"time\(\s*\d+\s*,\s*\d+\s*\)", script), script

    @pytest.mark.parametrize(
        "hh,mm",
        [
            (19, 54),
            (19, 55),  # the watcher's blackout opens; the MERGE must not defer
            (19, 59),
            (20, 0),
            (20, 30),
            (20, 59),
            (21, 0),
            (21, 1),
            (3, 0),
            (13, 0),
        ],
    )
    def test_the_workflow_agrees_with_merge_deferred_at_the_boundaries(
        self, tmp_path, hh, mm
    ):
        """Parity, executed: the step's own script against the constants it
        claims to read, at both inclusive edges and either side of them."""
        from datetime import datetime, timezone

        from scripts import check_triggers as ct

        now = datetime(2026, 9, 5, hh, mm, tzinfo=timezone.utc)
        result = subprocess.run(
            ["bash", "-c", self._step()["run"]],
            cwd=REPO_ROOT,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                "HOME": str(tmp_path),
                "NOW_UTC": now.isoformat(),
                "GITHUB_OUTPUT": str(tmp_path / "out"),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        expected = "true" if ct.merge_deferred(now) else "false"
        assert (tmp_path / "out").read_text().strip() == f"deferred={expected}", (
            result.stdout
        )
        if expected == "true":
            assert "deferred: inside the session window" in result.stdout

    def test_the_parity_check_can_fail(self, tmp_path):
        """Falsifiable control: the same script pointed at a checkout whose
        constants differ must disagree with the real `in_blackout`."""
        from datetime import datetime, timezone

        from scripts import check_triggers as ct

        fake = tmp_path / "checkout"
        (fake / "scripts").mkdir(parents=True)
        (fake / "scripts" / "check_triggers.py").write_text(
            "from datetime import time\n"
            "SESSION_START = time(1, 0)\n"
            "BLACKOUT_END = time(2, 0)\n"
        )
        now = datetime(2026, 9, 5, 20, 30, tzinfo=timezone.utc)
        assert ct.merge_deferred(now)
        result = subprocess.run(
            ["bash", "-c", self._step()["run"]],
            cwd=fake,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                "HOME": str(tmp_path),
                "NOW_UTC": now.isoformat(),
                "GITHUB_OUTPUT": str(tmp_path / "out"),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "out").read_text().strip() == "deferred=false"


class TestAutoMergeRefusesStaleFills:
    """Regression (round-1 review, 2026-09-05): a fallback branch that outlived
    a session could merge CLEAN on top of a main where the same order had been
    cancelled and re-executed at market — pending file deleted on both sides,
    inbox rows in different date files, the book holding the position twice.
    The session kind had "Reject stale sessions"; the watcher kind checked only
    its own diff. Now the files the branch fills are compared with the files
    main changed since their merge-base, and any overlap is a human decision."""

    STALE_TITLE = "auto-merge-session: a watcher fallback branch is stale against main"
    MERGE_TITLE = "auto-merge-session: a watcher fallback branch did not reach main"

    def _step(self) -> dict:
        return _auto_merge_step("Reject stale watcher fallback branches")

    def test_the_step_exists_and_is_gated_like_the_merge(self):
        step = self._step()
        assert step["id"] == "stale"
        cond = step["if"]
        assert "steps.inspect.outputs.kind == 'triggers'" in cond
        assert "already_merged != 'true'" in cond
        assert "steps.blackout.outputs.deferred != 'true'" in cond
        names = [s.get("name") for s in _auto_merge_spec()["jobs"]["merge"]["steps"]]
        assert names.index("Reject stale watcher fallback branches") < names.index(
            "Merge watcher fallback branch into main"
        )

    def test_a_stale_refusal_files_under_its_own_title(self):
        """A stale fill is a different fact from a broken merge; sharing one
        title would let a later clean merge close a decision nobody took."""
        report = _auto_merge_step("Report watcher-merge outcome")
        title = report["with"]["title"]
        assert "steps.stale.outputs.stale == 'true'" in title
        assert self.STALE_TITLE in title and self.MERGE_TITLE in title
        assert self.STALE_TITLE != self.MERGE_TITLE
        assert report["with"]["details"] == "${{ steps.stale.outputs.overlap }}"
        # Neither literal is used by any other workflow.
        for name in ALERTING_WORKFLOWS:
            if name == "auto-merge-session.yml":
                continue
            text = (REPO_ROOT / ".github" / "workflows" / name).read_text()
            assert self.STALE_TITLE not in text and self.MERGE_TITLE not in text, name

    # --- executed ----------------------------------------------------------

    def test_main_touching_the_filled_book_is_stale(self, tmp_path):
        repo, tip = _watcher_branch_repo(
            tmp_path, [("chore(triggers): execute ord_1 2026-09-05", A_FILL)]
        )
        _advance_main(
            tmp_path,
            "chore: weekday session 2026-09-05",
            {
                "data/portfolios/satoshi/portfolio.json": '{"cash": 1}\n',
                "data/orders/cancels/2026-09-05.jsonl": '{"target_order_id":"ord_1"}\n',
            },
        )
        result = _run_auto_merge_step(
            "Reject stale watcher fallback branches", repo, tip, tmp_path
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Stale fill" in result.stdout
        out = _outputs(tmp_path)
        assert "stale=true" in out
        assert "data/portfolios/satoshi/portfolio.json" in out, out
        # The overlap output is what reaches the issue; it must be the
        # multi-line form, or it truncates at the first newline.
        assert "overlap<<" in out

    def test_main_moving_elsewhere_is_not_stale(self, tmp_path):
        """fetch-ohlcv landing, another book trading: not this fill's business."""
        repo, tip = _watcher_branch_repo(
            tmp_path, [("chore(triggers): execute ord_1 2026-09-05", A_FILL)]
        )
        _advance_main(
            tmp_path,
            "[data] 2026-09-05 OHLCV update",
            {
                "data/market/ohlcv/BTC-EUR.jsonl": "{}\n",
                "data/portfolios/goldfinger/portfolio.json": "{}\n",
            },
        )
        result = _run_auto_merge_step(
            "Reject stale watcher fallback branches", repo, tip, tmp_path
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "stale=false" in _outputs(tmp_path)

    def test_the_leaderboard_artifact_is_not_a_staleness_signal(self, tmp_path):
        """Both sides regenerate current.json every time; counting it would
        make every branch that outlives a session stale by construction."""
        repo, tip = _watcher_branch_repo(
            tmp_path,
            [
                ("chore(triggers): execute ord_1 2026-09-05", A_FILL),
                (
                    "chore(triggers): execute fired/expired conditions 2026-09-05",
                    {"data/leaderboard/current.json": '{"branch": 1}\n'},
                ),
            ],
        )
        _advance_main(
            tmp_path,
            "chore: weekend refresh 2026-09-05",
            {"data/leaderboard/current.json": '{"main": 1}\n'},
        )
        result = _run_auto_merge_step(
            "Reject stale watcher fallback branches", repo, tip, tmp_path
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "stale=false" in _outputs(tmp_path)


class TestAutoMergeResolvesLeaderboardConflicts:
    """Regression (round-1 review, 2026-09-05): every fallback branch that
    outlived a session became unmergeable, because both sides rewrite
    `data/leaderboard/current.json` and a conflict was never retried — which
    then left the watcher in permanent refusal. That file is derived,
    self-healing state, so a merge whose ONLY conflict is that file takes
    main's copy and completes. Any other conflict still aborts."""

    STEP = "Merge watcher fallback branch into main"

    def test_the_resolution_is_scoped_to_the_one_derived_file(self):
        script = _auto_merge_step(self.STEP)["run"]
        assert "--diff-filter=U" in script
        assert 'conflicted" == "data/leaderboard/current.json"' in script
        assert "git checkout --ours -- data/leaderboard/current.json" in script
        # ...and the ledger conflict is still a human's problem.
        assert "merge --abort" in script and "exit 1" in script

    def _conflicting_branch(self, tmp_path: Path, branch_files: dict, main_files: dict):
        repo, tip = _watcher_branch_repo(
            tmp_path, [("chore(triggers): execute ord_1 2026-09-05", branch_files)]
        )
        _advance_main(tmp_path, "chore: weekday session 2026-09-05", main_files)
        return repo, tip

    def test_a_leaderboard_only_conflict_merges_with_mains_copy(self, tmp_path):
        repo, tip = self._conflicting_branch(
            tmp_path,
            {**A_FILL, "data/leaderboard/current.json": '{"who": "branch"}\n'},
            {"data/leaderboard/current.json": '{"who": "main"}\n'},
        )
        before = _bare_main(tmp_path)
        result = _run_auto_merge_step(
            self.STEP, repo, tip, tmp_path, VERIFIED_MAIN=_bare_main(tmp_path)
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Conflict only on data/leaderboard/current.json" in result.stdout
        after = _bare_main(tmp_path)
        assert after != before
        env = _git_env(tmp_path)
        bare = tmp_path / "bare.git"
        # The fill is on main, and main's leaderboard copy won.
        assert (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", tip, "main"], cwd=bare, env=env
            ).returncode
            == 0
        )
        shown = subprocess.run(
            ["git", "show", "main:data/leaderboard/current.json"],
            cwd=bare,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert shown == '{"who": "main"}\n'
        inbox = subprocess.run(
            ["git", "show", "main:data/orders/inbox/2026-09-05.jsonl"],
            cwd=bare,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "ord_1" in inbox

    def test_a_ledger_conflict_still_aborts(self, tmp_path):
        repo, tip = self._conflicting_branch(
            tmp_path,
            {**A_FILL, "data/leaderboard/current.json": '{"who": "branch"}\n'},
            {
                "data/leaderboard/current.json": '{"who": "main"}\n',
                "data/portfolios/satoshi/portfolio.json": '{"cash": "main"}\n',
            },
        )
        before = _bare_main(tmp_path)
        result = _run_auto_merge_step(
            self.STEP, repo, tip, tmp_path, VERIFIED_MAIN=_bare_main(tmp_path)
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "data/portfolios/satoshi/portfolio.json" in result.stdout
        assert _bare_main(tmp_path) == before, "a conflicting merge reached main"
        # The clone is left clean, not mid-merge.
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo,
            env=_git_env(tmp_path),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert status == "", status

    def test_a_clean_merge_still_works(self, tmp_path):
        repo, tip = self._conflicting_branch(
            tmp_path, A_FILL, {"data/market/ohlcv/BTC-EUR.jsonl": "{}\n"}
        )
        result = _run_auto_merge_step(
            self.STEP, repo, tip, tmp_path, VERIFIED_MAIN=_bare_main(tmp_path)
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", tip, "main"],
                cwd=tmp_path / "bare.git",
                env=_git_env(tmp_path),
            ).returncode
            == 0
        )


# --------------------------------------------------------------------------
# Round-2 review (2026-09-05) — the checks must bind the tip they were
# computed on, the merge must run before the evaluation, and a session's
# snapshot counts as touching the book
# --------------------------------------------------------------------------


class TestAutoMergeBindsTheTipItVerified:
    """Regression (round-2 review, 2026-09-05): the stale and scope checks ran
    once, before a `for attempt in 1 2 3` loop that re-fetched `main` and
    re-merged against a fresher tip on every rejected push — and a push is
    rejected only BECAUSE main moved, so every retry merged onto a ledger
    nothing had checked.

    Reproduced then in a scratch repo: branch B fills order `o1`; a session
    lands on main that CANCELS `o1` (pending file deleted on both sides, the
    CANCELLED_BY_AGENT row in a different dated file, the book untouched on
    main); git 3-way-merges it clean; main ends up holding a cancellation AND
    a fill for one order, and the book holds the position the agent believes
    it cancelled.
    """

    STEP = "Merge watcher fallback branch into main"

    def test_the_step_takes_the_verified_sha_from_the_stale_check(self):
        step = _auto_merge_step(self.STEP)
        assert step["env"]["VERIFIED_MAIN"] == "${{ steps.stale.outputs.main_sha }}"
        # ...and the stale check is the one that produces it.
        stale = _auto_merge_step("Reject stale watcher fallback branches")["run"]
        assert "main_sha=$(git rev-parse origin/main)" in stale
        assert 'echo "main_sha=$main_sha" >> "$GITHUB_OUTPUT"' in stale

    def test_there_is_no_blind_retry_loop(self):
        script = _auto_merge_step(self.STEP)["run"]
        assert "for attempt in" not in script, (
            "a retry re-merges against a tip no check has seen"
        )

    # --- executed ----------------------------------------------------------

    def test_a_main_that_moved_after_the_check_is_refused(self, tmp_path):
        """The money-path case: main cancelled the very order this branch
        fills, and the two diffs do not conflict."""
        repo, tip = _watcher_branch_repo(
            tmp_path, [("chore(triggers): execute ord_1 2026-09-05", A_FILL)]
        )
        verified = _bare_main(tmp_path)  # what the stale check would have seen
        _advance_main(
            tmp_path,
            "chore: weekday session 2026-09-05",
            {
                # exactly the shape that merges clean: the pending file is
                # deleted on both sides, the cancel row is its own file.
                "data/orders/pending/ord_1.json": None,
                "data/orders/cancels/2026-09-05.jsonl": '{"target_order_id":"ord_1"}\n',
            },
        )
        before = _bare_main(tmp_path)
        result = _run_auto_merge_step(
            self.STEP, repo, tip, tmp_path, VERIFIED_MAIN=verified
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "refusing to merge onto a tip nothing verified" in result.stdout
        assert _bare_main(tmp_path) == before, "the unverified merge reached main"

    def test_an_empty_verified_sha_is_refused(self, tmp_path):
        """Zero coverage is not a pass: if the stale step produced no sha it
        did not run, and nothing has checked this branch against main."""
        repo, tip = _watcher_branch_repo(
            tmp_path, [("chore(triggers): execute ord_1 2026-09-05", A_FILL)]
        )
        before = _bare_main(tmp_path)
        result = _run_auto_merge_step(self.STEP, repo, tip, tmp_path, VERIFIED_MAIN="")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "No verified main sha" in result.stdout
        assert _bare_main(tmp_path) == before


class TestStaleCheckCountsTheBookNotTheFile:
    """Regression (round-2 review, 2026-09-05): staleness was file overlap, and
    the daily session does not write the files a fill writes — it writes
    snapshots.json, journals, posts, baselines. So a branch that outlived a
    session merged CLEAN behind a snapshot valued WITHOUT its fill. Snapshots
    are immutable across sessions and `check_append_only.py` forbids
    correcting that row without `[restate]`, so the published valuation for
    that book stayed wrong by the fire-to-close P&L.
    """

    STEP = "Reject stale watcher fallback branches"

    def test_a_session_snapshot_for_the_same_book_is_stale(self, tmp_path):
        repo, tip = _watcher_branch_repo(
            tmp_path, [("chore(triggers): execute ord_1 2026-09-05", A_FILL)]
        )
        _advance_main(
            tmp_path,
            "chore: weekday session 2026-09-05",
            # A session's footprint: NO file the branch touched.
            {
                "data/portfolios/satoshi/snapshots.json": '[{"date": "2026-09-05"}]\n',
                "data/agent_memory/satoshi.md": "journal\n",
                "data/baselines/satoshi/benchmark.json": "[]\n",
            },
        )
        result = _run_auto_merge_step(self.STEP, repo, tip, tmp_path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Stale fill" in result.stdout
        out = _outputs(tmp_path)
        assert "stale=true" in out
        # Both sides of the book are named, so the reader can see the shape.
        assert "data/portfolios/satoshi/snapshots.json" in out, out
        assert "data/portfolios/satoshi/portfolio.json" in out, out

    def test_another_books_snapshot_is_not_stale(self, tmp_path):
        """Falsifiable control for the widened key: it must still be per book,
        not per `data/portfolios/`, or every branch is stale forever."""
        repo, tip = _watcher_branch_repo(
            tmp_path, [("chore(triggers): execute ord_1 2026-09-05", A_FILL)]
        )
        _advance_main(
            tmp_path,
            "chore: weekday session 2026-09-05",
            {"data/portfolios/goldfinger/snapshots.json": "[]\n"},
        )
        result = _run_auto_merge_step(self.STEP, repo, tip, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "stale=false" in _outputs(tmp_path)


class TestTheMergeIsDispatchedBeforeTheEvaluation:
    """Regression (round-2 review, 2026-09-05): a merge deferred inside the
    session window, or one whose dispatch was lost, cost the FOLLOWING sweep
    its own evaluation — `check_triggers.py` refuses while any `triggers/*`
    branch exists, and the merge was only dispatched after the watcher step.
    Every trigger and every expiry due that day then waited for the next
    dispatch. The action is now called before the run as well, and waits.
    """

    def test_the_action_waits_for_the_branch_to_disappear(self, tmp_path):
        rc, calls, out = _run_merge_fallback(
            tmp_path,
            ["triggers/2026-09-05-1"],
            wait_seconds=60,
            delete_after=["triggers/2026-09-05-1"],
        )
        assert rc == 0 and len(calls) == 1
        assert "All fallback branches merged" in out, out

    def test_a_branch_that_never_merges_is_a_warning_not_a_failure(self, tmp_path):
        """A deferred or stale merge must not fail this step: the watcher
        below refuses and says why, and the trailing call re-dispatches."""
        rc, calls, out = _run_merge_fallback(
            tmp_path, ["triggers/2026-09-05-1"], wait_seconds=16
        )
        assert rc == 0 and len(calls) == 1
        assert "::warning::" in out and "still on origin" in out, out

    def test_waiting_is_opt_in(self, tmp_path):
        """The trailing call must not burn runner minutes waiting for a merge
        nothing is about to consume."""
        rc, calls, out = _run_merge_fallback(tmp_path, ["triggers/2026-09-05-1"])
        assert rc == 0 and len(calls) == 1
        assert "Waiting up to" not in out, out


def test_the_venv_symlink_a_worktree_creates_is_ignored():
    """Regression (round-2 review, 2026-09-05): `.gitignore` said `.venv/`,
    and a trailing slash matches directories only. A git worktree's `.venv` is
    a SYMLINK to the main checkout's venv, so `git status` listed it untracked
    and a `git add -A` would have committed a local absolute path into the
    public repo — after which every fresh checkout carries a dangling `.venv`
    and both watchers' `python -m venv .venv` fails on a cache miss.
    """
    lines = [
        ln.strip()
        for ln in (REPO_ROOT / ".gitignore").read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert ".venv" in lines, "a bare `.venv` entry is what matches the symlink"
    assert ".venv/" not in lines, (
        "the directory-only form is what let the symlink through"
    )


def test_stale_check_when_main_has_not_moved_at_all(tmp_path):
    """The ordinary case, pinned because the conflict-key mapping runs over an
    empty `main_files` and an empty `comm` side is easy to get wrong."""
    repo, tip = _watcher_branch_repo(
        tmp_path, [("chore(triggers): execute ord_1 2026-09-05", A_FILL)]
    )
    result = _run_auto_merge_step(
        "Reject stale watcher fallback branches", repo, tip, tmp_path
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "stale=false" in _outputs(tmp_path)


class TestAutoMergeRefusesADispatchOutsideTheFallbackBranches:
    """Regression (round-3 review, 2026-09-05): `workflow_dispatch` was added
    for `.github/actions/merge-fallback-branches` (which dispatches with
    `--ref <branch>`) with no guard on the ref — and the Actions UI's "Run
    workflow" button defaults to the DEFAULT branch, which the issue body
    invites a reader towards ("open the failed run").

    Run on `main` the whole job was a green no-op that CLOSED the open issue:
    main's tip carries `chore(triggers): ` most days, so `inspect` classified
    it as a watcher branch; `git merge-base --is-ancestor main origin/main` is
    trivially true, so `already_merged` skipped scope, staleness and the
    merge; "Delete merged branch" ran `git push origin --delete main`
    (refused by GitHub, swallowed by `continue-on-error`); and the reporter,
    gated only on the kind, reported `success` — closing "a watcher fallback
    branch did not reach main" for a branch still sitting unmerged on origin,
    behind which every watcher run refuses to evaluate.
    """

    STEP = "Refuse a run outside the fallback branch patterns"

    def _run(self, ref: str, tmp_path: Path) -> subprocess.CompletedProcess:
        script = _auto_merge_step(self.STEP)["run"]
        return subprocess.run(
            ["bash", "-c", script],
            env={**_git_env(tmp_path), "REF": ref},
            capture_output=True,
            text=True,
        )

    def test_the_guard_runs_before_anything_classifies_a_commit(self):
        """First step, before the checkout: nothing may read main's tip and
        decide it is a watcher branch."""
        steps = _auto_merge_spec()["jobs"]["merge"]["steps"]
        assert steps[0].get("name") == self.STEP
        assert steps[0].get("if") is None, "an unconditional refusal, or it is none"

    def test_the_guard_names_exactly_the_branches_the_push_trigger_takes(self):
        """One list, two spellings: a pattern the workflow listens for but the
        guard refuses would break the session or watcher half outright, and a
        pattern the guard allows but nothing pushes is dead."""
        spec = _auto_merge_spec()
        on = spec[True] if True in spec else spec["on"]
        pushed = {b.replace("/**", "") for b in on["push"]["branches"]}
        script = _auto_merge_step(self.STEP)["run"]
        cased = {
            p.replace("/*", "")
            for p in re.search(r"\n\s*(\S+)\)\n", script).group(1).split("|")
        }
        assert cased == pushed, (cased, pushed)

    @pytest.mark.parametrize(
        "ref",
        [
            "refs/heads/triggers/2026-09-05-1234",
            "refs/heads/claude/dreamy-lovelace-6t8ifh",
        ],
    )
    def test_a_fallback_branch_passes(self, ref, tmp_path):
        result = self._run(ref, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize(
        "ref",
        [
            "refs/heads/main",  # the Actions UI's default — the reported case
            "refs/heads/fix/trigger-watcher-resilience",
            "refs/tags/attest/2026-09-05",
        ],
    )
    def test_anything_else_is_refused(self, ref, tmp_path):
        result = self._run(ref, tmp_path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "::error::" in result.stdout

    def test_a_refusal_files_nothing(self):
        """The reporter is gated on `steps.inspect.outputs.kind`, which is
        empty when this step failed — so a human's misfire goes red without
        touching the issue tracker."""
        cond = _auto_merge_step("Report watcher-merge outcome")["if"]
        assert "steps.inspect.outputs.kind == 'triggers'" in cond


class TestAutoMergeClosesTheIssueOnlyWhenNoBranchIsLeft:
    """Regression (round-3 review, 2026-09-05): the failure issue is one per
    CAUSE, not per branch, so a green run for one `triggers/*` branch closed
    the issue a DIFFERENT, still-unmerged branch had filed — while every
    watcher run kept refusing to evaluate behind that branch.
    """

    STEP = "Decide whether this run may report a recovery"

    def _run(
        self, repo: Path, tmp_path: Path, status: str, ref: str
    ) -> subprocess.CompletedProcess:
        script = _auto_merge_step(self.STEP)["run"]
        env = {
            **_git_env(tmp_path),
            "STATUS": status,
            "REF": ref,
            "GITHUB_OUTPUT": str(tmp_path / "out"),
        }
        return subprocess.run(
            ["bash", "-c", script], cwd=repo, env=env, capture_output=True, text=True
        )

    def test_the_reporter_is_gated_on_this_step(self):
        cond = _auto_merge_step("Report watcher-merge outcome")["if"]
        assert "steps.report_gate.outputs.report != 'false'" in cond
        assert _auto_merge_step(self.STEP)["id"] == "report_gate"
        # Same gate as the reporter, so it can never be the skipped step whose
        # empty output waves a recovery through.
        assert _auto_merge_step(self.STEP)["if"] == cond.rsplit(" && ", 1)[0]

    def test_a_failure_always_reports(self, tmp_path):
        repo, _ = _watcher_branch_repo(
            tmp_path, [("chore(triggers): execute ord_1 2026-09-05", A_FILL)]
        )
        result = self._run(repo, tmp_path, "failure", "refs/heads/triggers/a")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "report=true" in _outputs(tmp_path)

    def test_a_recovery_with_another_branch_still_on_origin_reports_nothing(
        self, tmp_path
    ):
        repo, tip = _watcher_branch_repo(
            tmp_path, [("chore(triggers): execute ord_1 2026-09-05", A_FILL)]
        )
        subprocess.run(
            ["git", "push", "-q", "origin", f"{tip}:refs/heads/triggers/2026-09-06-2"],
            cwd=repo,
            check=True,
            env=_git_env(tmp_path),
        )
        result = self._run(
            repo, tmp_path, "success", "refs/heads/triggers/2026-09-05-1"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "report=false" in _outputs(tmp_path)
        assert "triggers/2026-09-06-2" in result.stdout

    def test_a_recovery_with_a_clean_origin_reports_and_closes(self, tmp_path):
        """The control: the same success, with nothing left on origin, MUST
        still close the issue — a gate that never opens is a dead channel."""
        repo, _ = _watcher_branch_repo(
            tmp_path, [("chore(triggers): execute ord_1 2026-09-05", A_FILL)]
        )
        result = self._run(
            repo, tmp_path, "success", "refs/heads/triggers/2026-09-05-1"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "report=true" in _outputs(tmp_path)

    def test_the_branch_this_run_merged_does_not_hold_its_own_issue_open(
        self, tmp_path
    ):
        """A run's own branch may still be on origin when the delete step
        fails (it is `continue-on-error`), and that must not stop the
        recovery being reported."""
        repo, tip = _watcher_branch_repo(
            tmp_path, [("chore(triggers): execute ord_1 2026-09-05", A_FILL)]
        )
        subprocess.run(
            ["git", "push", "-q", "origin", f"{tip}:refs/heads/triggers/2026-09-05-1"],
            cwd=repo,
            check=True,
            env=_git_env(tmp_path),
        )
        result = self._run(
            repo, tmp_path, "success", "refs/heads/triggers/2026-09-05-1"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "report=true" in _outputs(tmp_path)

    def test_an_unreadable_origin_is_not_a_recovery(self, tmp_path):
        """Unknown is never all-clear: if origin cannot be listed, whether a
        branch is still waiting is unknown, so nothing closes."""
        repo, _ = _watcher_branch_repo(
            tmp_path, [("chore(triggers): execute ord_1 2026-09-05", A_FILL)]
        )
        subprocess.run(
            ["git", "remote", "set-url", "origin", str(tmp_path / "gone.git")],
            cwd=repo,
            check=True,
            env=_git_env(tmp_path),
        )
        result = self._run(
            repo, tmp_path, "success", "refs/heads/triggers/2026-09-05-1"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "report=false" in _outputs(tmp_path)
