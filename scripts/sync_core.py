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
    # Hardcoded to the live cast (the-manager, satoshi, EUR schedule, real
    # personas under .claude/agents, €10k/€1M capital). They validate the live
    # desk's specific configuration, not the reusable engine, so they cannot
    # pass against the demo desk. Reclassified during SP4 isolation validation.
    "test_allocator_config.py",
    "test_backward_compat.py",
    "test_baseline_manager.py",
    "test_baselines.py",
    "test_blog.py",
    "test_check_triggers.py",
    "test_daily_log.py",
    "test_jurisdiction_drivers.py",
    "test_laboratory_pipeline.py",
    "test_live_switch.py",
    "test_manager_context.py",
    "test_manager_context_golden.py",
    "test_manager_report.py",
    "test_output_bundle.py",
    "test_paper_broker.py",
    "test_persona_dispatch.py",
    "test_portfolio_summaries.py",
    "test_posts.py",
    "test_roster_parity.py",
    "test_tax_shadow.py",
    "test_universe_drift.py",
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


def apply(core: Path, root: Path = LIVE_ROOT) -> None:
    for rel in apply_manifest(root):
        src, dst = root / rel, core / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


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
        print(f"[sync_core] applied {len(apply_manifest())} files -> {core}")
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
