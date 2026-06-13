"""Tests for scripts.attest_ledger — deterministic ledger hash computation.

NOTE: This test suite does NOT test the YAML workflow or git push behavior —
those are untestable in a Python unit test context. The attestation artifact
(annotated git tag + GitHub Actions run summary) is the source of truth; the
test suite only verifies the hash computation and rendering are deterministic,
correct, and tamper-sensitive.

TDD: failing tests written before the implementation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Allow importing scripts/ from the project root
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.attest_ledger import compute_ledger_digest, render_attestation


# ---------------------------------------------------------------------------
# Helpers — build a fixture directory tree that mirrors the real ledger dirs
# ---------------------------------------------------------------------------


def _build_fixture_tree(tmp_path: Path) -> Path:
    """Create a minimal ledger tree under tmp_path and return the root."""
    root = tmp_path / "repo"
    root.mkdir()

    # data/orders/outbox
    (root / "data" / "orders" / "outbox").mkdir(parents=True)
    (root / "data" / "orders" / "outbox" / "2026-06-10.jsonl").write_text(
        '{"order_id": "ord_1", "ticker": "AAPL"}\n'
    )
    (root / "data" / "orders" / "outbox" / "2026-06-11.jsonl").write_text(
        '{"order_id": "ord_2", "ticker": "MSFT"}\n'
    )

    # data/orders/inbox
    (root / "data" / "orders" / "inbox").mkdir(parents=True)
    (root / "data" / "orders" / "inbox" / "2026-06-10.jsonl").write_text(
        '{"order_id": "ord_1", "status": "filled"}\n'
    )

    # data/orders/pending
    (root / "data" / "orders" / "pending").mkdir(parents=True)
    (root / "data" / "orders" / "pending" / "ord_3.json").write_text(
        '{"order_id": "ord_3", "trigger": {"op": ">=", "level": 200}}\n'
    )

    # data/orders/cancels
    (root / "data" / "orders" / "cancels").mkdir(parents=True)

    # data/portfolios
    (root / "data" / "portfolios" / "ada-usd").mkdir(parents=True)
    (root / "data" / "portfolios" / "ada-usd" / "portfolio.json").write_text(
        '{"cash": 9000.0}\n'
    )
    (root / "data" / "portfolios" / "btc-eur").mkdir(parents=True)
    (root / "data" / "portfolios" / "btc-eur" / "portfolio.json").write_text(
        '{"cash": 8500.0}\n'
    )

    # data/leaderboard/current.json
    (root / "data" / "leaderboard").mkdir(parents=True)
    (root / "data" / "leaderboard" / "current.json").write_text(
        '{"updated_at": "2026-06-12T00:00:00Z"}\n'
    )

    return root


# ---------------------------------------------------------------------------
# Determinism: same tree → same digest_root across two calls
# ---------------------------------------------------------------------------


def test_compute_ledger_digest_is_deterministic(tmp_path: Path) -> None:
    """Calling compute_ledger_digest twice on the same tree must return identical digest_root."""
    root = _build_fixture_tree(tmp_path)
    digest_a = compute_ledger_digest(root)
    digest_b = compute_ledger_digest(root)
    assert digest_a["digest_root"] == digest_b["digest_root"]


# ---------------------------------------------------------------------------
# Tamper sensitivity: one byte change → different digest_root
# ---------------------------------------------------------------------------


def test_changed_file_changes_digest_root(tmp_path: Path) -> None:
    """Mutating one byte in a ledger file must change the digest_root."""
    root = _build_fixture_tree(tmp_path)
    digest_before = compute_ledger_digest(root)

    # Mutate one file
    outbox_file = root / "data" / "orders" / "outbox" / "2026-06-10.jsonl"
    original = outbox_file.read_text()
    outbox_file.write_text(original.replace("ord_1", "ord_X"))

    digest_after = compute_ledger_digest(root)
    assert digest_before["digest_root"] != digest_after["digest_root"]


# ---------------------------------------------------------------------------
# file_count and total_bytes correct on the fixture tree
# ---------------------------------------------------------------------------


def test_file_count_and_total_bytes(tmp_path: Path) -> None:
    """file_count and total_bytes must match the actual fixture files."""
    root = _build_fixture_tree(tmp_path)
    digest = compute_ledger_digest(root)

    # Count expected files: outbox(2) + inbox(1) + pending(1) + cancels(0) +
    # portfolios(2) + leaderboard/current.json(1) = 7
    assert digest["file_count"] == 7

    # total_bytes must equal the sum of actual file sizes
    expected_bytes = sum(
        f.stat().st_size
        for f in root.rglob("*")
        if f.is_file() and _is_in_ledger_dirs(f, root)
    )
    assert digest["total_bytes"] == expected_bytes


def _is_in_ledger_dirs(file: Path, root: Path) -> bool:
    """Mirror the ledger path logic from compute_ledger_digest."""
    rel = file.relative_to(root)
    parts = rel.parts
    if len(parts) >= 2 and parts[0] == "data" and parts[1] == "orders":
        return True
    if len(parts) >= 2 and parts[0] == "data" and parts[1] == "portfolios":
        return True
    if (
        len(parts) == 3
        and parts[0] == "data"
        and parts[1] == "leaderboard"
        and parts[2] == "current.json"
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# relpath sorting is stable (same order across calls)
# ---------------------------------------------------------------------------


def test_relpath_sorting_is_stable(tmp_path: Path) -> None:
    """File paths in digest['files'] must appear in lexicographic order."""
    root = _build_fixture_tree(tmp_path)
    digest = compute_ledger_digest(root)
    relpaths = list(digest["files"].keys())
    assert relpaths == sorted(relpaths)


# ---------------------------------------------------------------------------
# Digest output shape
# ---------------------------------------------------------------------------


def test_digest_output_shape(tmp_path: Path) -> None:
    """compute_ledger_digest must return all required keys."""
    root = _build_fixture_tree(tmp_path)
    digest = compute_ledger_digest(root)

    assert "generated_for_date" in digest
    assert "file_count" in digest
    assert "total_bytes" in digest
    assert "digest_root" in digest
    assert "files" in digest
    assert isinstance(digest["files"], dict)
    # Each file entry must be a 64-char hex string (sha256)
    for relpath, file_hash in digest["files"].items():
        assert len(file_hash) == 64, f"Bad hash length for {relpath}"
        assert all(c in "0123456789abcdef" for c in file_hash), (
            f"Non-hex hash for {relpath}"
        )


# ---------------------------------------------------------------------------
# Empty ledger dirs (no files at all) — should not crash
# ---------------------------------------------------------------------------


def test_empty_ledger_dirs_no_crash(tmp_path: Path) -> None:
    """compute_ledger_digest on an empty (but present) ledger tree must not raise."""
    root = tmp_path / "empty_repo"
    root.mkdir()
    # Create the dirs but leave them empty
    (root / "data" / "orders" / "outbox").mkdir(parents=True)
    (root / "data" / "orders" / "inbox").mkdir(parents=True)
    (root / "data" / "orders" / "pending").mkdir(parents=True)
    (root / "data" / "orders" / "cancels").mkdir(parents=True)
    (root / "data" / "portfolios").mkdir(parents=True)
    (root / "data" / "leaderboard").mkdir(parents=True)

    digest = compute_ledger_digest(root)
    assert digest["file_count"] == 0
    assert digest["total_bytes"] == 0
    assert len(digest["digest_root"]) == 64  # sha256 of empty string is still a hash


# ---------------------------------------------------------------------------
# render_attestation includes digest_root and date
# ---------------------------------------------------------------------------


def test_render_attestation_includes_digest_root_and_date(tmp_path: Path) -> None:
    """render_attestation must include the digest_root and generated_for_date."""
    root = _build_fixture_tree(tmp_path)
    digest = compute_ledger_digest(root)
    rendered = render_attestation(digest)

    assert digest["digest_root"] in rendered
    assert digest["generated_for_date"] in rendered


def test_render_attestation_includes_file_count_and_bytes(tmp_path: Path) -> None:
    """render_attestation must mention file_count and total_bytes."""
    root = _build_fixture_tree(tmp_path)
    digest = compute_ledger_digest(root)
    rendered = render_attestation(digest)

    assert str(digest["file_count"]) in rendered
    assert str(digest["total_bytes"]) in rendered


def test_render_attestation_is_nonempty_string(tmp_path: Path) -> None:
    """render_attestation must return a non-empty string."""
    root = _build_fixture_tree(tmp_path)
    digest = compute_ledger_digest(root)
    rendered = render_attestation(digest)

    assert isinstance(rendered, str)
    assert len(rendered) > 0


# ---------------------------------------------------------------------------
# Files outside ledger dirs are excluded
# ---------------------------------------------------------------------------


def test_files_outside_ledger_dirs_excluded(tmp_path: Path) -> None:
    """Files in data/cache/ or data/market/ must NOT appear in the digest."""
    root = _build_fixture_tree(tmp_path)

    # Add a file that must be excluded
    (root / "data" / "cache").mkdir(parents=True)
    (root / "data" / "cache" / "something.json").write_text('{"noise": true}\n')
    (root / "data" / "market").mkdir(parents=True)
    (root / "data" / "market" / "today.json").write_text('{"price": 100}\n')

    digest = compute_ledger_digest(root)
    for relpath in digest["files"]:
        assert not relpath.startswith("data/cache"), (
            f"cache file leaked into digest: {relpath}"
        )
        assert not relpath.startswith("data/market"), (
            f"market file leaked into digest: {relpath}"
        )
