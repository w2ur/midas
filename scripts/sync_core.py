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
    # Price-data bootstrap for forks: config-driven (no live-cast coupling), it
    # populates data/market/ohlcv from yfinance. Shipping it also un-breaks the
    # `midas fetch-ohlcv` subcommand, which maps to scripts.fetch_ohlcv.
    "fetch_ohlcv.py",
    # Required by daily_session's module-level import, so it ships whether or
    # not a fork wants it — omitting it makes core's daily_session unimportable.
    # The staleness logic it enforces is cadence-generic, not live-cast-coupled.
    "session_guard.py",
    "resolve_manager_outcomes.py",
    "backfill_baselines.py",
    "build_tax_shadow.py",
    # Restatement tooling: reusable by any fork that later corrects a price-
    # store defect and needs to re-derive published valuations from it. Both
    # are config-driven (engine.config.get_config()) with no live-cast
    # coupling. restate_bundles.py imports restate_valuations.py directly, so
    # the two ship together; tests/test_restate_bundles.py already syncs
    # unconditionally (the tests glob is independent of CORE_SCRIPTS) and
    # would otherwise fail to import on the mirror.
    "restate_valuations.py",
    "restate_bundles.py",
    # Same rationale one layer down: restate_* re-derive *valuations* from a
    # corrected store, this one corrects the *fills* underneath when the defect
    # reached execution — a notional converted at the wrong quote currency.
    # Config-driven, no live-cast coupling, and it imports restate_valuations
    # for the inception-capital back-solve so the two must ship together.
    "reconcile_quote_currency.py",
    # One-shot store migration, shipped for the same reason as the restatement
    # tooling: a fork whose store predates the ISO-at-ingest contract needs it
    # to convert, and it is config-driven with no live-cast coupling. Marker-
    # guarded, so shipping it cannot cause a double-division downstream.
    "normalise_store_units.py",
    "run_backtest.py",
    "run_all_combos.py",
    "refresh_universes.py",
]

LIVE_ONLY_TESTS = {
    # Import a live-only script that core does not ship.
    "test_attest_ledger.py",
    "test_attest_verify.py",
    "test_backfill_snapshots.py",
    "test_fetch_sentiment.py",
    "test_refresh_leaderboard.py",
    # Read the committed OHLCV store (data/market/ohlcv), not shipped to core.
    "test_fetch_market_data.py",
    "test_manager_session.py",
    # Imports scripts.sync_core, the dev-only tool not in core.
    "test_sync_core.py",
    # Drives scripts/prompt_hash.py against docs/triggers/weekday-session.md.
    # Both are live-desk RemoteTrigger infrastructure: core has no trigger doc
    # and prompt_hash.py is not in CORE_SCRIPTS.
    "test_prompt_hash.py",
    # Drives scripts/bootstrap_venv.sh, live sandbox infrastructure. CORE_SCRIPTS
    # ships .py modules only, so the shell script has no route into core.
    "test_bootstrap_venv.py",
    # Reads this repo's .github/workflows/ and backtester/, neither of which
    # exists in core (core ships its own .github/ as a core-native file).
    "test_ci_guards.py",
    # Reads the committed portfolios, pending orders, inbox ledger and
    # universes — live desk state, none of which core ships.
    "test_rails_live_coverage.py",
    # Drives scripts/check_append_only.py, live-repo CI infrastructure that is
    # not in CORE_SCRIPTS.
    "test_append_only_gate.py",
    # Same shape: drives scripts/check_session_freshness.py, the session-integrity
    # guard. Live CI infrastructure, not in CORE_SCRIPTS.
    "test_session_freshness.py",
    # Imports app/, the Streamlit dashboard. Core ships the engine and the
    # orchestration, not this desk's local UI.
    "test_app_formatting.py",
    # NOTE: the 21 formerly-live-only cast-coupled tests were reclaimed into
    # core in SP5. They ship to core byte-identical; the ones that assert on the
    # live cast carry @pytest.mark.live_cast and skip on the demo desk (see
    # tests/conftest.py). The rest run against the demo desk too.
}

# `ticker_currencies.json` is the HAND-MAINTAINED override map — currency
# resolution layer 1, where a human decision is recorded — and stays
# byte-guarded. `tickers.json` is layer 2, the machine-populated vendor
# registry, and lives in the regenerated tier below.
GENERIC_DATA_FILES = ["data/ticker_currencies.json"]
# Universes stay here so `apply` copies ALL of them and `check` byte-compares
# every one it is not told to exempt — see REGENERATED_DATA_GLOBS, which names
# only the seven scraped indexes. The two lists overlap deliberately;
# apply_manifest dedupes.
GENERIC_DATA_GLOBS = ["data/strategies/*.json", "data/universes/*.json"]

#: Generic data that live REGENERATES on a schedule. `apply` seeds it into core
#: so a fresh fork starts with a working ticker registry and universe lists;
#: `check` verifies each is present, not that it is byte-identical.
#:
#: Byte-equality here is an invariant a cron legitimately breaks. Live's
#: `refresh-universes.yml` (cron `15 03 * * 1`, Mondays 03:15 UTC) rescrapes
#: these from Wikipedia and commits whatever the page says that week; nothing
#: propagates that to core; `core-drift-guard` (cron `17 6 * * 1`, Mondays
#: 06:17 UTC) then runs the same morning and goes red. It did exactly that on
#: 2026-08-10 on `sp500.json` and `nasdaq100.json`. A guard a scheduled job is
#: guaranteed to trip weekly teaches everyone to ignore it — the failure
#: `.github/actions/failure-issue` was built to end, reintroduced one layer up.
#: (Those are the declared crons. The observed starts that Monday were 04:55
#: and 08:05 UTC — GitHub's scheduler runs 42 min to 6 h late, which is why the
#: schedule is cited here and a single observed pair is not.)
#:
#: NAMED INDIVIDUALLY, not globbed. `data/universes/*.json` would also exempt
#: congressional/insider/high-short, and those are NOT scraped: they are
#: regenerated deterministically from constants in `engine/universes/
#: alternative.py` (e.g. `refresh_congressional` is
#: `sorted(_CONGRESSIONAL_FALLBACK)`), and that module is itself byte-synced to
#: core via `code_manifest`. So live and core produce identical bytes for them
#: by construction, they can never legitimately drift, and exempting them would
#: hide a genuinely stale or hand-edited copy behind a green guard.
#:
#: This does NOT weaken W2.9. Its point was that `apply` and `check` must agree
#: about what the mirror contains, and its real target was the two ticker maps
#: (currency-resolution layers 1 and 2), which stay byte-guarded along with the
#: strategy specs. Here the two halves are brought back into agreement by
#: correcting what `apply` PROMISES — a seed, not a mirror — rather than by
#: letting `check` look away. Core ships `refresh_universes.py` (it is in
#: CORE_SCRIPTS), so a fork regenerates these itself; index composition is not
#: a correctness surface the way a currency map is.
REGENERATED_DATA_GLOBS = [
    # The vendor ticker registry, rewritten by `save_registry` on EVERY
    # fetch-ohlcv run and staged since 2026-08-11. Yahoo returns unstable name
    # strings ("Crédit Agricole S.A." vs "CREDIT AGRICOLE", "adidas AG" vs
    # "adidas AG    N"), so the first staged run churned 111 lines and the next
    # will churn more — nightly, not weekly. Byte-guarding it would put
    # core-drift-guard permanently red, which is the failure this tier exists
    # to prevent. Core ships fetch_ohlcv.py, so a fork regenerates its own on
    # first fetch; the seeded copy is a bootstrap, and a stale registry is
    # strictly better than an absent one. NOT the same call as layer 1 above.
    "data/tickers.json",
    "data/universes/cac40.json",
    "data/universes/dax.json",
    "data/universes/dow30.json",
    "data/universes/ftse100.json",
    "data/universes/nasdaq100.json",
    "data/universes/sp500.json",
    "data/universes/stoxx600.json",
]

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


def _expand(patterns: list[str], root: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        base, glob = pattern.rsplit("/", 1)
        for p in sorted((root / base).glob(glob)):
            files.append(p.relative_to(root))
    return files


def regenerated_manifest(root: Path = LIVE_ROOT) -> list[Path]:
    """Generic data seeded into core but not held byte-identical.

    See REGENERATED_DATA_GLOBS for why these are presence-checked only.
    """
    return _rel_sorted(_expand(REGENERATED_DATA_GLOBS, root))


def apply_manifest(root: Path = LIVE_ROOT) -> list[Path]:
    """Everything copied to core: code_manifest() + generic (non-moat) data.

    Includes the regenerated data — `apply` really does copy it, as a seed.
    `check` is where the two tiers diverge.
    """
    files = list(code_manifest(root))
    for rel in GENERIC_DATA_FILES:
        files.append(Path(rel))
    files.extend(_expand(GENERIC_DATA_GLOBS, root))
    files.extend(regenerated_manifest(root))
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
        # Demo-desk source (roster, personas) is synced from live and prunable;
        # its data/ subtree is a core-managed test fixture (universe resolvers
        # regenerate it on the demo desk) that live never populates — leave it.
        return len(parts) > 2 and parts[2] != "data"
    if (
        parts[0] == "data"
        and len(parts) == 3
        and parts[1] in ("strategies", "universes")
    ):
        return rel.suffix == ".json"
    return False


def _assert_not_live_root(core: Path, root: Path) -> None:
    """Refuse any destructive op when `core` resolves to the live source tree.

    prune() unlinks owned-tree files absent from the manifest; on the live root
    that would delete the live-only scripts/tests and sync_core.py itself. Guard
    against `apply --core <live-checkout>` typos.
    """
    if core.resolve() == root.resolve():
        raise ValueError(
            f"refusing to operate: --core path {core} is the live source root"
        )


def prune(
    core: Path, root: Path = LIVE_ROOT, keep: set[Path] | None = None
) -> list[Path]:
    """Delete core files in owned trees that are no longer in apply_manifest()."""
    _assert_not_live_root(core, root)
    if keep is None:
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
    _assert_not_live_root(core, root)
    manifest = apply_manifest(root)
    for rel in manifest:
        src, dst = root / rel, core / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    removed = prune(core, root, keep=set(manifest))
    if removed:
        print(f"[sync_core] pruned {len(removed)} stale file(s)")


def check(core: Path, root: Path = LIVE_ROOT) -> list[Path]:
    """Files that differ between live and core, over the FULL apply manifest.

    Iterates `apply_manifest`, not `code_manifest` (2026-08-07 review, W2.9).
    `apply` copies the generic data files — `data/ticker_currencies.json`,
    `data/tickers.json`, the strategy and universe JSON — and `check` did not
    look at them, so the two halves of the mirror contract disagreed about
    what the mirror contains. Those two ticker maps are currency-resolution
    **layers 1 and 2**: a fork resolving a ticker through a stale copy of them
    prices it in the wrong currency, which is the 2026-08-07 defect exactly.
    They were the least-guarded files in the manifest and the most consequential.

    Two tiers. Most of the manifest must be byte-identical. The regenerated
    data (REGENERATED_DATA_GLOBS) is checked for PRESENCE only — live rewrites
    it on a schedule and core has its own copy of the tool that produces it, so
    requiring equality would go red every Monday by design. A *missing* seed is
    still drift: `apply` promised to put it there.
    """
    regenerated = set(regenerated_manifest(root))
    drift: list[Path] = []
    for rel in apply_manifest(root):
        src, dst = root / rel, core / rel
        if not src.exists():
            continue  # a glob that matched nothing here is not core's drift
        if not dst.exists():
            drift.append(rel)
        elif rel not in regenerated and not filecmp.cmp(src, dst, shallow=False):
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
        print(
            f"[sync_core] DRIFT: {len(drift)} manifest file(s) differ:",
            file=sys.stderr,
        )
        for rel in drift:
            print(f"  {rel}", file=sys.stderr)
        return 1
    print("[sync_core] in sync (apply manifest matches)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
