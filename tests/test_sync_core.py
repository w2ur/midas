from __future__ import annotations
from pathlib import Path
import pytest
from scripts import sync_core

REL = lambda p: Path(p)


def test_manifest_includes_whole_engine_package():
    m = set(sync_core.apply_manifest())
    for f in (
        "engine/config.py",
        "engine/paper_broker.py",
        "engine/universes/index.py",
        "engine/managers/__init__.py",
        "engine/selectors/__init__.py",
    ):
        assert REL(f) in m, f"{f} missing from manifest"


def test_manifest_core_scripts_only():
    m = set(sync_core.apply_manifest())
    assert REL("scripts/daily_session.py") in m
    assert REL("scripts/fetch_market_data.py") in m  # in daily_session's closure
    # fetch_ohlcv ships: it is the config-driven price-data bootstrap for forks
    # and the target of the `midas fetch-ohlcv` subcommand.
    assert REL("scripts/fetch_ohlcv.py") in m
    for live_only in (
        "fetch_sentiment",
        "attest_ledger",
        "refresh_leaderboard",
        "backfill_snapshots",
    ):
        assert REL(f"scripts/{live_only}.py") not in m


def test_manifest_excludes_live_only_tests():
    m = set(sync_core.apply_manifest())
    assert REL("tests/test_orders.py") in m  # a hermetic engine test stays in core
    for t in (
        # import a live-only script
        "test_attest_ledger",
        "test_backfill_snapshots",
        "test_fetch_sentiment",
        "test_refresh_leaderboard",
        # read the committed OHLCV store
        "test_fetch_market_data",
        "test_manager_session",
        # imports the dev-only sync_core tool
        "test_sync_core",
    ):
        assert REL(f"tests/{t}.py") not in m


def test_manifest_ships_generic_data_not_track_record():
    m = set(sync_core.apply_manifest())
    assert REL("data/universes/sp500.json") in m
    assert any(str(p).startswith("data/strategies/") for p in m)
    assert REL("data/tickers.json") in m
    for moat in (
        "data/market",
        "data/portfolios",
        "data/baselines",
        "data/output",
        "data/agent_memory",
        "data/orders",
        "data/posts",
        "data/blog",
    ):
        assert not any(str(p).startswith(moat) for p in m), f"{moat} must not ship"


def test_code_manifest_is_data_free_subset():
    apply_m, code_m = set(sync_core.apply_manifest()), set(sync_core.code_manifest())
    assert code_m <= apply_m
    assert not any(str(p).startswith("data/") for p in code_m)
    assert REL("engine/config.py") in code_m
    assert REL("pyproject.toml") in code_m


def test_apply_then_check_is_clean(tmp_path):
    sync_core.apply(tmp_path)
    assert (tmp_path / "engine" / "config.py").is_file()
    assert sync_core.check(tmp_path) == []


def test_check_detects_code_drift(tmp_path):
    sync_core.apply(tmp_path)
    (tmp_path / "engine" / "config.py").write_text("# tampered\n", encoding="utf-8")
    assert REL("engine/config.py") in sync_core.check(tmp_path)


def test_check_ignores_generic_data_drift(tmp_path):
    sync_core.apply(tmp_path)
    (tmp_path / "data" / "universes" / "sp500.json").write_text("[]", encoding="utf-8")
    assert sync_core.check(tmp_path) == []  # data is synced but not guarded


def test_main_check_exits_nonzero_on_drift(tmp_path):
    sync_core.apply(tmp_path)
    (tmp_path / "engine" / "config.py").write_text("# tampered\n", encoding="utf-8")
    assert sync_core.main(["check", "--core", str(tmp_path)]) == 1


def test_manifest_excludes_its_own_test():
    # tests/test_sync_core.py imports scripts.sync_core, which is a dev/CI-only
    # tool never shipped to core; its test must not enter the manifest either.
    apply_m, code_m = set(sync_core.apply_manifest()), set(sync_core.code_manifest())
    assert REL("tests/test_sync_core.py") not in apply_m
    assert REL("tests/test_sync_core.py") not in code_m


def test_prune_removes_stale_owned_files_only(tmp_path):
    core = tmp_path / "core"
    # Seed a stale engine module, a stale test, and core-native files.
    (core / "engine").mkdir(parents=True)
    (core / "engine" / "obsolete.py").write_text("# gone\n")
    (core / "tests").mkdir(parents=True)
    (core / "tests" / "test_obsolete.py").write_text("def test_x(): pass\n")
    (core / "roster.yaml").write_text("globals: {}\nagents: {}\n")  # core-native
    (core / "LICENSE").write_text("MIT\n")  # core-native

    sync_core.apply(core)  # copies the real manifest AND prunes stale owned files

    assert not (core / "engine" / "obsolete.py").exists()  # pruned
    assert not (core / "tests" / "test_obsolete.py").exists()  # pruned
    assert (core / "roster.yaml").read_text() == "globals: {}\nagents: {}\n"  # kept
    assert (core / "LICENSE").exists()  # kept
    assert (core / "engine" / "config.py").exists()  # real manifest copied


def test_prune_leaves_synced_manifest_files(tmp_path):
    core = tmp_path / "core"
    sync_core.apply(core)
    # A second prune with no drift removes nothing.
    assert sync_core.prune(core) == []


def test_prune_spares_demo_desk_data_fixtures(tmp_path):
    # examples/demo-desk/data/ is a core-managed test fixture that live never
    # populates (its universe resolvers regenerate it on the demo desk); prune
    # must not delete it, even though it is not in live's manifest.
    core = tmp_path / "core"
    fixture = core / "examples" / "demo-desk" / "data" / "universes" / "sp500.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('["AAPL", "MSFT"]')
    # A stale demo-desk *source* file (not under data/) is still pruned.
    stale_persona = core / "examples" / "demo-desk" / ".claude" / "agents" / "gone.md"
    stale_persona.parent.mkdir(parents=True)
    stale_persona.write_text("# stale\n")

    sync_core.apply(core)

    assert fixture.exists()  # data/ fixture spared
    assert not stale_persona.exists()  # stale demo-desk source pruned


def test_apply_refuses_live_source_root():
    # A `apply --core <live-root>` typo would let prune() delete the live-only
    # scripts/tests and sync_core.py itself. Guard must reject it before copying.
    with pytest.raises(ValueError, match="live source root"):
        sync_core.apply(sync_core.LIVE_ROOT)


def test_prune_refuses_live_source_root():
    with pytest.raises(ValueError, match="live source root"):
        sync_core.prune(sync_core.LIVE_ROOT)


def test_cast_tests_reclaimed_into_manifest():
    reclaimed = {
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
    # None of the reclaimed tests remain live-only.
    assert reclaimed & sync_core.LIVE_ONLY_TESTS == set()
    # The 12 genuinely un-runnable tests stay live-only.
    assert sync_core.LIVE_ONLY_TESTS == {
        "test_attest_ledger.py",
        "test_attest_verify.py",
        "test_backfill_snapshots.py",
        "test_fetch_sentiment.py",
        "test_refresh_leaderboard.py",
        "test_fetch_market_data.py",
        "test_manager_session.py",
        "test_sync_core.py",
        "test_bootstrap_venv.py",
        # Asserts on this repo's .github/ and backtester/, neither of which
        # core has (core ships its own .github/ as a core-native file).
        "test_ci_guards.py",
        # Asserts on the live desk's committed holdings, pending orders and
        # inbox ledger — core ships none of that state.
        "test_rails_live_coverage.py",
        # Drives scripts/check_append_only.py, a live-repo CI tool.
        "test_append_only_gate.py",
    }
    # All reclaimed tests now ship in the code manifest.
    manifest_names = {p.name for p in sync_core.code_manifest()}
    assert reclaimed <= manifest_names


def test_manifest_is_closed_under_scripts_imports():
    """Every scripts.* module a shipped file imports must itself ship.

    Origin: 2026-08-02. The session-guard fix added a module-level
    `from scripts.session_guard import ...` to scripts/daily_session.py.
    daily_session.py was in CORE_SCRIPTS, session_guard.py was not, so the next
    `sync_core.py apply` would have published a midas-core whose daily_session
    raised ImportError on import — in the PUBLIC repo. The manifest recorded
    closure only as a hand-written comment ("in daily_session's closure"), which
    no test enforced. This enforces it.
    """
    import ast

    manifest = set(sync_core.apply_manifest())
    shipped_py = sorted(
        p for p in manifest if p.suffix == ".py" and p.parts[0] in ("scripts", "engine")
    )
    assert shipped_py, "expected the manifest to ship python files"

    missing: list[str] = []
    for rel in shipped_py:
        tree = ast.parse((sync_core.LIVE_ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "scripts"
            ):
                parts = node.module.split(".")
                if len(parts) > 1:
                    targets.append(parts[1])
                else:
                    # `from scripts import x` — each alias is a module.
                    targets.extend(a.name for a in node.names)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[0] == "scripts" and len(parts) > 1:
                        targets.append(parts[1])
            for mod in targets:
                if REL(f"scripts/{mod}.py") not in manifest:
                    missing.append(f"{rel} imports scripts.{mod}, which is not shipped")

    assert not missing, "manifest not closed under imports:\n  " + "\n  ".join(
        sorted(set(missing))
    )
