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
    python scripts/attest_ledger.py [--date YYYY-MM-DD] [--json]

With no flags, prints the rendered attestation block + digest_root.
With ``--json``, prints the full digest JSON.
"""

from __future__ import annotations

import argparse
import hashlib
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
    digest_root = hashlib.sha256(roll_up_input.encode()).hexdigest()

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
    """Compute per top-level-directory file_count and total_bytes from the digest."""
    # Group by the first two path components (e.g. "data/orders")
    buckets: dict[str, dict] = {}
    for relpath, _hash in digest["files"].items():
        parts = Path(relpath).parts
        # Use the first two components as the bucket key, fall back to parts[0]
        bucket_key = "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
        if bucket_key not in buckets:
            buckets[bucket_key] = {"file_count": 0, "total_bytes": 0}
        file_path = Path(relpath)
        # We need the root to get the actual size — but we computed total_bytes
        # already from the file objects; here we only have relpaths in the
        # digest dict.  For rendering we approximate per-dir bytes by looking
        # at what we have stored: we don't have per-file sizes in the digest
        # dict, so we only report file counts in the subtotals and omit bytes.
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
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

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
