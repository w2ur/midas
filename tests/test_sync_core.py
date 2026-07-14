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
    for live_only in (
        "fetch_ohlcv",
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
    # The 7 genuinely un-runnable tests stay live-only.
    assert sync_core.LIVE_ONLY_TESTS == {
        "test_attest_ledger.py",
        "test_backfill_snapshots.py",
        "test_fetch_sentiment.py",
        "test_refresh_leaderboard.py",
        "test_fetch_market_data.py",
        "test_manager_session.py",
        "test_sync_core.py",
    }
    # All reclaimed tests now ship in the code manifest.
    manifest_names = {p.name for p in sync_core.code_manifest()}
    assert reclaimed <= manifest_names
