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
    def test_every_backtester_pin_matches_the_root_lockfile(self):
        """The narrowing may drop packages; it may never re-resolve versions."""
        root = _pins(_ROOT / "requirements.txt")
        service = _pins(_ROOT / "backtester" / "requirements.txt")
        assert service, "backtester/requirements.txt has no pins — parser broke"
        drift = {
            name: (version, root[name])
            for name, version in service.items()
            if name in root and root[name] != version
        }
        assert drift == {}, (
            "backtester/requirements.txt must copy the root lockfile's versions "
            f"verbatim; these differ (service, root): {drift}"
        )

    def test_backtester_introduces_no_package_the_root_lockfile_lacks(self):
        """A service-only package would be installed at a version CI never ran."""
        root = _pins(_ROOT / "requirements.txt")
        service = _pins(_ROOT / "backtester" / "requirements.txt")
        orphans = sorted(set(service) - set(root))
        assert orphans == [], (
            "these are pinned for the service but absent from the root "
            f"lockfile, so nothing tests them: {orphans}"
        )

    def test_every_requirement_is_an_exact_pin(self):
        """The 2026-08-05 defect's shape: a requirement present but unpinned."""
        for name in ("requirements.txt", "backtester/requirements.txt"):
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
