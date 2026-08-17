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
]


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
        # `if: always()` — without it the step is skipped on the failure it exists to report.
        assert step.get("if") == "always()", (
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
            f"failure-issue title {title!r} has no fallback for an unresolved "
            "mode"
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
