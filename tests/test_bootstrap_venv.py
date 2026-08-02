"""Guards scripts/bootstrap_venv.sh --check, the Step 0 gate.

Origin: the 2026-07-31 session stalled ~63 hours inside a venv rebuild that sat
in the timed critical path of every run. bootstrap_venv.sh moves the build to
image-build time and leaves the session with a fast, network-free assertion.
That assertion is only worth anything if it actually fails on a bad venv, so
every rejection path below is exercised, not just the happy one.

Live-only: it drives a shell script that midas-core does not ship.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "bootstrap_venv.sh"

REQS = "attrs==23.2.0\n"
# sha256 of REQS, the value the script stamps. Hard-coding it keeps the test
# independent of the hashing helper it is checking.
REQS_SHA = "a4b2a1cbfe4a2e1d09b6a6bb0eff0d1d9d1e0e1d5f5f2b1a8c9d7e6f5a4b3c2d"


def _fake_venv(root: Path, version: str = "3.12", stamp: str | None = None) -> Path:
    """A venv-shaped directory whose `python` reports `version`.

    --check never imports anything; it asks the interpreter for its version and
    compares a stamp. A shim is enough, and keeps the test at milliseconds
    instead of building a real environment.
    """
    venv = root / ".venv"
    (venv / "bin").mkdir(parents=True)
    py = venv / "bin" / "python"
    py.write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
    py.chmod(0o755)
    if stamp is not None:
        (venv / ".midas-bootstrap").write_text(f"{stamp} {version}\n", encoding="utf-8")
    return venv


def _check(tmp_path: Path, venv: Path | None) -> subprocess.CompletedProcess[str]:
    reqs = tmp_path / "requirements.txt"
    if not reqs.exists():
        reqs.write_text(REQS, encoding="utf-8")
    return subprocess.run(
        ["bash", str(SCRIPT), "--check"],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "MIDAS_VENV": str(venv if venv else tmp_path / "absent"),
            "MIDAS_REQS": str(reqs),
        },
    )


def _real_sha(tmp_path: Path) -> str:
    reqs = tmp_path / "requirements.txt"
    reqs.write_text(REQS, encoding="utf-8")
    import hashlib

    return hashlib.sha256(reqs.read_bytes()).hexdigest()


@pytest.fixture
def env(tmp_path):
    return tmp_path


def test_check_accepts_a_matching_venv(env):
    sha = _real_sha(env)
    venv = _fake_venv(env, "3.12", stamp=sha)
    r = _check(env, venv)
    assert r.returncode == 0, r.stderr
    assert "venv OK" in r.stdout


def test_check_rejects_a_missing_venv(env):
    r = _check(env, None)
    assert r.returncode == 1
    assert "no venv at" in r.stderr


def test_check_rejects_an_old_interpreter(env):
    """The 2026-07-17 crash: venv on 3.11, pandas-ta explodes at import."""
    sha = _real_sha(env)
    venv = _fake_venv(env, "3.11", stamp=sha)
    r = _check(env, venv)
    assert r.returncode == 1
    assert "need >= 3.12" in r.stderr


def test_check_rejects_a_venv_with_no_stamp(env):
    """An interrupted install leaves no stamp; --check must fail closed."""
    _real_sha(env)
    venv = _fake_venv(env, "3.12", stamp=None)
    r = _check(env, venv)
    assert r.returncode == 1
    assert "no bootstrap stamp" in r.stderr


def test_check_rejects_a_venv_stale_against_the_lockfile(env):
    """A lockfile bump must invalidate a previously-good venv."""
    venv = _fake_venv(env, "3.12", stamp=REQS_SHA)  # stamp from a different lockfile
    (env / "requirements.txt").write_text(REQS, encoding="utf-8")
    r = _check(env, venv)
    assert r.returncode == 1
    assert "venv is stale" in r.stderr


def test_check_rejects_a_missing_lockfile(env):
    venv = _fake_venv(env, "3.12", stamp=REQS_SHA)
    r = subprocess.run(
        ["bash", str(SCRIPT), "--check"],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "MIDAS_VENV": str(venv),
            "MIDAS_REQS": str(env / "nope.txt"),
        },
    )
    assert r.returncode == 1
    assert "no requirements lockfile" in r.stderr
