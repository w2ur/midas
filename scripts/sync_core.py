# scripts/sync_core.py
"""Sync the midas-core manifest from midas-live (source of truth) into a
midas-core checkout. A dev/CI tool ONLY — it is never imported on the live
runtime path.

    python scripts/sync_core.py apply --core /path/to/midas-core
    python scripts/sync_core.py check --core /path/to/midas-core   # exit 1 on drift
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

LIVE_ROOT = Path(__file__).resolve().parents[1]

CORE_SCRIPTS = [
    "__init__.py",
    "daily_session.py",
    "check_triggers.py",
    "session_state.py",
    "fetch_market_data.py",
    "resolve_manager_outcomes.py",
    "backfill_baselines.py",
    "build_tax_shadow.py",
    "run_backtest.py",
    "run_all_combos.py",
    "refresh_universes.py",
]

LIVE_ONLY_TESTS = {
    # Import a live-only script that core does not ship.
    "test_attest_ledger.py",
    "test_backfill_snapshots.py",
    "test_fetch_sentiment.py",
    "test_refresh_leaderboard.py",
    # Read the committed OHLCV store (data/market/ohlcv), not shipped to core.
    "test_fetch_market_data.py",
    "test_manager_session.py",
    # Imports scripts.sync_core, the dev-only tool not in core.
    "test_sync_core.py",
    # NOTE: the 21 formerly-live-only cast-coupled tests were reclaimed into
    # core in SP5. They ship to core byte-identical; the ones that assert on the
    # live cast carry @pytest.mark.live_cast and skip on the demo desk (see
    # tests/conftest.py). The rest run against the demo desk too.
}

GENERIC_DATA_FILES = ["data/ticker_currencies.json", "data/tickers.json"]
GENERIC_DATA_GLOBS = ["data/strategies/*.json", "data/universes/*.json"]

TOP_LEVEL = ["pyproject.toml", "requirements.in", "requirements.txt"]


def _rel_sorted(paths):
    return sorted(set(paths), key=str)


def code_manifest(root: Path = LIVE_ROOT) -> list[Path]:
    """Code + config that must never diverge between live and core."""
    files: list[Path] = []
    for p in (root / "engine").rglob("*.py"):
        if "__pycache__" not in p.parts:
            files.append(p.relative_to(root))
    for name in CORE_SCRIPTS:
        files.append(Path("scripts") / name)
    for p in (root / "examples" / "demo-desk").rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts:
            files.append(p.relative_to(root))
    files.append(Path("tests") / "__init__.py")
    files.append(Path("tests") / "conftest.py")
    for p in sorted((root / "tests").glob("test_*.py")):
        if p.name not in LIVE_ONLY_TESTS:
            files.append(p.relative_to(root))
    for name in TOP_LEVEL:
        files.append(Path(name))
    return _rel_sorted(files)


def apply_manifest(root: Path = LIVE_ROOT) -> list[Path]:
    """Everything copied to core: code_manifest() + generic (non-moat) data."""
    files = list(code_manifest(root))
    for rel in GENERIC_DATA_FILES:
        files.append(Path(rel))
    for pattern in GENERIC_DATA_GLOBS:
        base, glob = pattern.rsplit("/", 1)
        for p in sorted((root / base).glob(glob)):
            files.append(p.relative_to(root))
    return _rel_sorted(files)


# Trees whose contents the manifest fully owns in core. Pruning deletes files
# HERE that are not in the current apply_manifest(). Core-native files
# (roster.yaml, README.md, LICENSE, DISCLAIMER.md, .github/, .gitignore) live
# OUTSIDE these trees and are never touched.
_OWNED_TREES = ("engine", "scripts", "tests", "examples/demo-desk")


def _is_owned(rel: Path) -> bool:
    """True if rel is a prune candidate — a file in manifest-owned territory."""
    parts = rel.parts
    if parts[0] == "engine":
        return rel.suffix == ".py"
    if parts[0] == "scripts":
        return rel.suffix == ".py"  # core scripts/ holds only CORE_SCRIPTS
    if parts[0] == "tests":
        return rel.name == "conftest.py" or (
            rel.name.startswith(("test_", "__init__")) and rel.suffix == ".py"
        )
    if parts[:2] == ("examples", "demo-desk"):
        return True
    if (
        parts[0] == "data"
        and len(parts) == 3
        and parts[1] in ("strategies", "universes")
    ):
        return rel.suffix == ".json"
    return False


def prune(core: Path, root: Path = LIVE_ROOT) -> list[Path]:
    """Delete core files in owned trees that are no longer in apply_manifest()."""
    keep = set(apply_manifest(root))
    removed: list[Path] = []
    scan_dirs = list(_OWNED_TREES) + ["data/strategies", "data/universes"]
    for tree in scan_dirs:
        base = core / tree
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            rel = p.relative_to(core)
            if _is_owned(rel) and rel not in keep:
                p.unlink()
                removed.append(rel)
    return _rel_sorted(removed)


def apply(core: Path, root: Path = LIVE_ROOT) -> None:
    for rel in apply_manifest(root):
        src, dst = root / rel, core / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    removed = prune(core, root)
    if removed:
        print(f"[sync_core] pruned {len(removed)} stale file(s)")


def check(core: Path, root: Path = LIVE_ROOT) -> list[Path]:
    drift: list[Path] = []
    for rel in code_manifest(root):
        src, dst = root / rel, core / rel
        if not dst.exists() or not filecmp.cmp(src, dst, shallow=False):
            drift.append(rel)
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sync_core")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("apply", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("--core", required=True, type=Path)
    ns = parser.parse_args(argv)
    core = ns.core.resolve()
    if ns.cmd == "apply":
        apply(core)
        print(f"[sync_core] applied {len(apply_manifest())} files (+prune) -> {core}")
        return 0
    drift = check(core)
    if drift:
        print(f"[sync_core] DRIFT: {len(drift)} code file(s) differ:", file=sys.stderr)
        for rel in drift:
            print(f"  {rel}", file=sys.stderr)
        return 1
    print("[sync_core] in sync (code manifest matches)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
