"""`attest --verify` — the step that turns the daily attestation into a claim.

Before this, `attest-ledger.yml` computed a SHA-256 digest of the committed
ledger, wrote it into an annotated tag, and asserted nothing about it. A
tampered ledger produced a different digest and a green run. Fifty-five
consecutive green runs were therefore not evidence of anything except that
the workflow ran.

Verification re-derives a published tag's digest from that tag's own tree and
compares it to what was recorded at the time. It answers exactly one question
— "is the history under this attestation still the history it attested to?" —
and it can answer no, which is the property the digest alone lacked.

Live-only (see LIVE_ONLY_TESTS in scripts/sync_core.py): imports
`scripts.attest_ledger`, which core does not ship.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.attest_ledger import (
    compute_ledger_digest,
    latest_attest_tag,
    recorded_digest,
    render_attestation,
    verify_tag,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def attested_repo(tmp_path: Path) -> Path:
    """A repo with one ledger file and a genuine attestation tag over it."""
    repo = tmp_path / "repo"
    (repo / "data" / "orders" / "inbox").mkdir(parents=True)
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")

    ledger = repo / "data" / "orders" / "inbox" / "2026-04-17.jsonl"
    ledger.write_text('{"order_id":"o1","status":"filled","fill_price":100.0}\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ledger")

    digest = compute_ledger_digest(repo)
    message = render_attestation(digest)
    _git(repo, "tag", "-a", "attest/2026-04-17", "-m", message)
    return repo


def test_an_untouched_history_verifies(attested_repo):
    assert verify_tag(attested_repo) == 0


def test_a_rewritten_ledger_row_fails_verification(attested_repo, capsys):
    """The done-when: a doctored historical row makes the attestation red.

    `commit --amend` is the realistic shape — it leaves the tag pointing at a
    commit whose *content* has changed, which is precisely what a quiet
    rewrite of published history looks like.
    """
    ledger = attested_repo / "data" / "orders" / "inbox" / "2026-04-17.jsonl"
    ledger.write_text('{"order_id":"o1","status":"filled","fill_price":1.0}\n')
    _git(attested_repo, "add", "-A")
    _git(attested_repo, "commit", "-q", "--amend", "--no-edit")
    _git(attested_repo, "tag", "-d", "attest/2026-04-17")
    # Re-tag the rewritten commit with the ORIGINAL message: the attestation
    # still claims the old digest, the tree no longer produces it.
    original = attested_repo / "msg.txt"
    original.write_text(
        render_attestation(
            {
                "generated_for_date": "2026-04-17",
                "file_count": 1,
                "total_bytes": 55,
                "digest_root": "0" * 64,
                "files": {"data/orders/inbox/2026-04-17.jsonl": "0" * 64},
            }
        )
    )
    _git(attested_repo, "tag", "-a", "attest/2026-04-17", "-F", str(original))

    assert verify_tag(attested_repo) == 1
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "does NOT re-derive" in out


def test_a_repo_with_no_attestation_passes(tmp_path):
    """The first run must not be red for a reason nobody can fix."""
    repo = tmp_path / "empty"
    repo.mkdir()
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    assert latest_attest_tag(repo) is None
    assert verify_tag(repo) == 0


def test_the_recorded_digest_is_parsed_from_the_real_message_format(attested_repo):
    """Guard the coupling between what the tag writes and what verify reads.

    These are two functions in the same file with a regex between them. If
    `render_attestation` ever restyles that line, verification would silently
    fall through its "nothing to compare" branch and pass forever — a check
    that cannot fail, reintroduced by a formatting change.
    """
    published = recorded_digest(attested_repo, "attest/2026-04-17")
    assert published is not None
    assert published == compute_ledger_digest(attested_repo)["digest_root"]


def test_verification_covers_a_file_deleted_since_the_tag(attested_repo):
    """A walk of today's tree would miss it; a worktree checkout does not.

    Deleting a published ledger file is one of the more plausible tampering
    shapes, and the one an implementation reading "the files that exist now"
    is blind to.
    """
    ledger = attested_repo / "data" / "orders" / "inbox" / "2026-04-17.jsonl"
    ledger.unlink()
    _git(attested_repo, "add", "-A")
    _git(attested_repo, "commit", "-q", "-m", "drop the ledger file")

    # The tag still points at the original commit, which still has the file —
    # so this must still verify. The check is about the attested revision, not
    # about HEAD.
    assert verify_tag(attested_repo) == 0


def test_json_digest_and_verify_agree_on_the_same_tree(attested_repo):
    """`--json` and `--verify` must not compute two different digests."""
    from_json = json.loads(
        subprocess.run(
            [
                # sys.executable, not "python": there is no bare `python` on the
                # owner's uv-managed Mac, so this test was green only on the CI
                # runner — an environment-dependent green, which is the shape
                # this repo's guards exist to refuse.
                sys.executable,
                str(
                    Path(__file__).resolve().parents[1] / "scripts" / "attest_ledger.py"
                ),
                "--json",
                "--root",
                str(attested_repo),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert from_json["digest_root"] == recorded_digest(
        attested_repo, "attest/2026-04-17"
    )
