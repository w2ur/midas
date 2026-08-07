"""Compute a deterministic tamper-evidence digest of the committed trade ledger.

This script is PURE — no git operations, no network calls, no file writes.
The attestation artifact is produced by the GitHub Actions workflow
``.github/workflows/attest-ledger.yml``, which uses this script to generate
the digest, then creates an annotated tag on the current ``main`` HEAD and
pushes it to GitHub.

Tamper-evidence mechanism
--------------------------
GitHub's immutable push timestamp on the annotated tag, combined with the
run log / step summary from the GitHub Actions job, serves as the third-party
attestation.  A "you backdated your git history" accusation would require
compromising:
  1. GitHub's tag push event timeline (immutable after the fact), AND
  2. The GitHub Actions run log (immutable after execution).

True GPG signing is NOT available on stock GitHub-hosted runners (the runner
has no private key material to sign with).  An annotated tag whose message
carries the digest, pushed to GitHub, is the practical equivalent: the
commitment is GitHub's server-side timestamp, not a cryptographic signature.

Digest coverage
----------------
The following ledger paths are included, sorted lexicographically for
determinism:
  - ``data/orders/``  (outbox, inbox, pending, cancels — recursively)
  - ``data/portfolios/``  (all files recursively)
  - ``data/leaderboard/current.json``  (single file)

Files outside those paths are excluded.

``digest_root`` naming
-----------------------
The top-level roll-up is named ``digest_root``, not ``merkle_root``, to avoid
overclaiming a full Merkle tree structure.  It is computed as the SHA-256 of
the sorted ``relpath:sha256`` lines joined by newlines — a simple, deterministic
concatenation hash that is sufficient for tamper detection.

Usage
-----
    python scripts/attest_ledger.py [--date YYYY-MM-DD] [--json] [--root DIR]
    python scripts/attest_ledger.py --render-from FILE

With no flags, prints the rendered attestation block + digest_root.
With ``--json``, prints the full digest JSON.
With ``--render-from FILE``, reads a saved digest JSON and prints the rendered
attestation block (no recomputation — guarantees the tag message and the step
summary are derived from the identical digest object).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import tempfile
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Core digest computation
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 of a file's contents."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_ledger_paths(root: Path) -> list[Path]:
    """Return all ledger file paths under root, sorted lexicographically.

    Covered directories:
      - data/orders/  (outbox, inbox, pending, cancels)
      - data/portfolios/
      - data/leaderboard/current.json  (single file)
    """
    files: list[Path] = []

    orders_dir = root / "data" / "orders"
    if orders_dir.is_dir():
        for f in orders_dir.rglob("*"):
            if f.is_file():
                files.append(f)

    portfolios_dir = root / "data" / "portfolios"
    if portfolios_dir.is_dir():
        for f in portfolios_dir.rglob("*"):
            if f.is_file():
                files.append(f)

    leaderboard_file = root / "data" / "leaderboard" / "current.json"
    if leaderboard_file.is_file():
        files.append(leaderboard_file)

    # Lexicographic sort on the relative path string for full determinism
    files.sort(key=lambda p: str(p.relative_to(root)))
    return files


def compute_ledger_digest(root: Path) -> dict:
    """Compute a deterministic digest of the committed trade ledger.

    Parameters
    ----------
    root:
        Repository root directory.  The ledger paths are resolved relative
        to this directory.

    Returns
    -------
    dict with keys:
        generated_for_date  str  ISO date (YYYY-MM-DD, UTC)
        file_count          int  total number of covered files
        total_bytes         int  sum of file sizes in bytes
        digest_root         str  64-char hex sha256 roll-up over sorted entries
        files               dict[str, str]  relpath → sha256 mapping
    """
    files = _collect_ledger_paths(root)

    per_file: dict[str, str] = {}
    total_bytes = 0

    for path in files:
        relpath = str(path.relative_to(root))
        per_file[relpath] = _sha256_file(path)
        total_bytes += path.stat().st_size

    # digest_root = sha256 over sorted "relpath:hash\n" lines.
    # Even when files is empty, we still hash the empty string so the output
    # type is consistent (64-char hex).
    roll_up_input = "".join(
        f"{relpath}:{file_hash}\n" for relpath, file_hash in sorted(per_file.items())
    )
    digest_root = hashlib.sha256(roll_up_input.encode("utf-8")).hexdigest()

    return {
        "generated_for_date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
        "file_count": len(per_file),
        "total_bytes": total_bytes,
        "digest_root": digest_root,
        "files": per_file,
    }


# ---------------------------------------------------------------------------
# Per-directory subtotals (used in render_attestation)
# ---------------------------------------------------------------------------


def _subtotals(digest: dict) -> dict[str, dict]:
    """Compute per top-level-directory file_count from the digest."""
    # Group by the first two path components (e.g. "data/orders")
    buckets: dict[str, dict] = {}
    for relpath, _hash in digest["files"].items():
        parts = Path(relpath).parts
        # Use the first two components as the bucket key, fall back to parts[0]
        bucket_key = "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
        if bucket_key not in buckets:
            buckets[bucket_key] = {"file_count": 0}
        # Per-file sizes are not stored in the digest dict; only counts are reported.
        buckets[bucket_key]["file_count"] += 1

    return buckets


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_attestation(digest: dict) -> str:
    """Render a compact human-readable attestation block.

    Suitable for use as the annotated git tag message and the GitHub Actions
    step summary.

    Parameters
    ----------
    digest:
        Output of ``compute_ledger_digest``.

    Returns
    -------
    str — multi-line attestation block.
    """
    lines: list[str] = [
        "=== Midas Ledger Attestation ===",
        f"date:         {digest['generated_for_date']}",
        f"file_count:   {digest['file_count']}",
        f"total_bytes:  {digest['total_bytes']}",
        f"digest_root:  {digest['digest_root']}",
        "",
        "--- per-directory file counts ---",
    ]

    subtotals = _subtotals(digest)
    for bucket, stats in sorted(subtotals.items()):
        lines.append(f"  {bucket}: {stats['file_count']} file(s)")

    lines.append("")
    lines.append(
        "digest_root = sha256(sorted relpath:sha256 lines) — tamper-evident roll-up"
    )
    lines.append("attestation: GitHub annotated tag push timestamp + Actions run log")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute and print the Midas ledger attestation digest.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Override the generated_for_date label (default: today UTC).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the full digest JSON instead of the rendered attestation.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=_PROJECT_ROOT,
        help="Repository root (default: project root derived from this script's location).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Re-derive the most recent attest/* tag's digest from that tag's own "
            "tree and compare it to the digest recorded in the tag message at the "
            "time. Exit 1 on divergence — history was rewritten under a published "
            "attestation. Without this the workflow computed a digest and asserted "
            "nothing about it: a tampered ledger produced a different hash and a "
            "green run."
        ),
    )
    parser.add_argument(
        "--verify-tag",
        metavar="TAG",
        help="Verify this tag instead of the most recent attest/* tag.",
    )
    parser.add_argument(
        "--render-from",
        metavar="FILE",
        dest="render_from",
        help=(
            "Read a saved digest JSON (produced by --json) and print the rendered "
            "attestation block.  No recomputation — guarantees tag message and step "
            "summary are derived from the identical digest object."
        ),
    )
    return parser



# ---------------------------------------------------------------------------
# Verification — what makes the daily attestation an assertion
# ---------------------------------------------------------------------------

#: The rendered block writes `digest_root:  <hex>`; that line is the whole
#: commitment, so it is what gets parsed back out.
_RECORDED_DIGEST = re.compile(r"^digest_root:\s+([0-9a-f]{64})\s*$", re.M)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


def latest_attest_tag(root: Path) -> str | None:
    """The most recent `attest/*` tag by tag date, or None if there are none."""
    out = _git(root, "tag", "--list", "attest/*", "--sort=-creatordate")
    tags = [line.strip() for line in out.splitlines() if line.strip()]
    return tags[0] if tags else None


def recorded_digest(root: Path, tag: str) -> str | None:
    """The digest_root written into the tag's message when it was created."""
    message = _git(root, "tag", "-l", "--format=%(contents)", tag)
    match = _RECORDED_DIGEST.search(message)
    return match.group(1) if match else None


def digest_at_tag(root: Path, tag: str) -> str:
    """Recompute the digest from the tag's own tree.

    Uses a detached worktree rather than reading blobs one by one: the digest
    is defined over "the ledger files present at this revision", and a
    worktree is the only cheap way to reproduce that set exactly — including
    files that have since been deleted, which a walk of today's tree would
    silently omit.
    """
    with tempfile.TemporaryDirectory() as tmp:
        checkout = Path(tmp) / "tree"
        _git(root, "worktree", "add", "--detach", "--quiet", str(checkout), tag)
        try:
            return compute_ledger_digest(checkout)["digest_root"]
        finally:
            _git(root, "worktree", "remove", "--force", str(checkout))


def verify_tag(root: Path, tag: str | None = None) -> int:
    """Compare a published attestation against the tree it attested to.

    Returns a process exit code. A repository with no attest tag yet, or a tag
    whose message predates the `digest_root:` line, is reported and passes —
    there is nothing to compare, and failing would make the first run of this
    check red for a reason nobody can fix.
    """
    tag = tag or latest_attest_tag(root)
    if tag is None:
        print("attest --verify: no attest/* tag exists yet — nothing to verify.")
        return 0

    published = recorded_digest(root, tag)
    if published is None:
        print(f"attest --verify: {tag} records no digest_root — nothing to verify.")
        return 0

    rederived = digest_at_tag(root, tag)
    if rederived == published:
        print(f"attest --verify: {tag} re-derives to its published digest.")
        print(f"  digest_root: {published}")
        return 0

    print(f"::error::{tag} does NOT re-derive to its published digest.")
    print(f"  published at tag time: {published}")
    print(f"  re-derived now:        {rederived}")
    print(
        "\nThe committed ledger at that revision is not what was attested to. "
        "Either history was rewritten under a published attestation, or the "
        "digest definition changed — both need explaining before the next "
        "attestation is meaningful."
    )
    return 1


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.verify:
        raise SystemExit(verify_tag(args.root.resolve(), args.verify_tag))

    if args.render_from:
        with open(args.render_from, encoding="utf-8") as f:
            digest = json.load(f)
        print(render_attestation(digest))
        return

    root: Path = args.root.resolve()
    digest = compute_ledger_digest(root)

    if args.date:
        digest["generated_for_date"] = args.date

    if args.as_json:
        print(json.dumps(digest, indent=2))
    else:
        block = render_attestation(digest)
        print(block)
        print()
        print(f"DIGEST_ROOT={digest['digest_root']}")


if __name__ == "__main__":
    main()
