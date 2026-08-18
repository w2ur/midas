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


def test_check_catches_generic_data_drift(tmp_path):
    """This test asserted the opposite until 2026-08-07 (W2.9) — that data was
    "synced but not guarded". It was pinning the gap, not a decision: `apply`
    copies these files, so `check` claiming the mirror is in sync while they
    differ is the two halves of one contract disagreeing. The two ticker maps
    are currency-resolution layers 1 and 2 — the consequential case, and the
    one W2.9 was actually aimed at."""
    sync_core.apply(tmp_path)
    assert sync_core.check(tmp_path) == []
    (tmp_path / "data" / "ticker_currencies.json").write_text("{}", encoding="utf-8")
    assert sync_core.check(tmp_path) == [Path("data/ticker_currencies.json")]


def test_check_catches_drift_in_a_glob_matched_generic_file(tmp_path):
    """The GENERIC_DATA_GLOBS path needs its own case, not just the named files.

    The test above covers a `GENERIC_DATA_FILES` entry. Without this one,
    moving `data/strategies/*.json` into the presence-only tier would leave the
    whole suite green — verified: making exactly that edit passed 22 + 32 tests
    before this was added.
    """
    sync_core.apply(tmp_path)
    specs = sorted((tmp_path / "data" / "strategies").glob("*.json"))
    assert specs, "no strategy specs were applied"

    specs[0].write_text("{}", encoding="utf-8")

    assert sync_core.check(tmp_path) == [Path("data/strategies") / specs[0].name]


def test_the_alternative_universes_stay_byte_guarded(tmp_path):
    """Only the SCRAPED indexes are exempt.

    congressional/insider/high-short regenerate deterministically from
    constants in engine/universes/alternative.py, which is itself byte-synced
    to core — so live and core produce identical bytes by construction and a
    divergence there is a real defect, not a weekly refresh.
    """
    sync_core.apply(tmp_path)
    exempt = {str(p) for p in sync_core.regenerated_manifest()}
    for name in ("congressional", "insider", "high-short"):
        assert f"data/universes/{name}.json" not in exempt

    (tmp_path / "data" / "universes" / "congressional.json").write_text(
        "[]", encoding="utf-8"
    )

    assert sync_core.check(tmp_path) == [Path("data/universes/congressional.json")]


def test_regenerated_data_is_seeded_but_not_held_byte_identical(tmp_path):
    """Regression: 5599e64f6 — a guard a cron was guaranteed to trip weekly.

    `refresh-universes.yml` rewrites `data/universes/*.json` in live every
    Monday 04:53 UTC; `core-drift-guard` runs 08:05 UTC the same morning and
    went red on `sp500.json` + `nasdaq100.json` on 2026-08-10. Nothing
    propagates live's refresh to core and nothing should — core ships
    `refresh_universes.py` and regenerates its own.
    """
    sync_core.apply(tmp_path)
    universes = tmp_path / "data" / "universes"
    assert (universes / "sp500.json").is_file(), "apply must still seed them"

    (universes / "sp500.json").write_text("[]", encoding="utf-8")

    assert sync_core.check(tmp_path) == []


def test_a_missing_regenerated_seed_is_still_drift(tmp_path):
    """Presence-only is not no-check: `apply` promised to put the file there.

    Without this, dropping universes from the manifest entirely would be
    indistinguishable from seeding them correctly.
    """
    sync_core.apply(tmp_path)
    (tmp_path / "data" / "universes" / "sp500.json").unlink()

    assert sync_core.check(tmp_path) == [Path("data/universes/sp500.json")]


def test_regenerated_data_is_a_strict_subset_of_what_apply_copies(tmp_path):
    """The two halves of the contract must still name the same files.

    W2.9's principle, kept: a path exempted from byte-equality must still be
    one `apply` actually writes, or `check` is exempting something that was
    never there.
    """
    assert set(sync_core.regenerated_manifest()) <= set(sync_core.apply_manifest())
    assert sync_core.regenerated_manifest(), "the exemption list matched nothing"


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
    # The genuinely un-runnable tests stay live-only. (This comment said "12"
    # against a 13-entry set; the count is asserted by the literal below, so
    # restating it in prose only ever adds something else to keep in step.)
    assert sync_core.LIVE_ONLY_TESTS == {
        # Imports app/, the Streamlit dashboard, which core does not ship.
        "test_app_formatting.py",
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
        # Drives scripts/check_session_freshness.py, the same shape: a
        # live-repo CI tool backing session-integrity, absent from CORE_SCRIPTS.
        "test_session_freshness.py",
        # Drives scripts/prompt_hash.py against docs/triggers/, both live-desk
        # RemoteTrigger infrastructure that core does not carry.
        "test_prompt_hash.py",
        # Reads workers/trigger-gate/, the Cloudflare dispatch-gate: live-desk
        # quota infrastructure keyed to this repo's workflow file and allocator
        # channels, neither of which core has.
        "test_trigger_gate_parity.py",
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


# ---------------------------------------------------------------------------
# check() covers the data files too (2026-08-07 review, W2.9)
# ---------------------------------------------------------------------------


def test_check_iterates_the_full_apply_manifest(tmp_path):
    """`apply` copies the generic data files and `check` did not look at
    them, so the two halves of the mirror contract disagreed about what the
    mirror contains.

    The probe is `data/ticker_currencies.json` — currency-resolution layer 1,
    the HAND-MAINTAINED override map, where a human decision is recorded. It
    was `data/tickers.json` (layer 2) until 2026-08-11, when that file moved to
    the presence-only tier: `fetch-ohlcv` started staging the registry it
    rewrites on every run, and Yahoo's unstable name strings churn ~110 lines a
    night. Byte-guarding it would have put core-drift-guard permanently red.
    The gap W2.9 closed is unchanged — a data file in `apply_manifest` but not
    `code_manifest` is still byte-checked; see the companion test for the other
    tier."""
    core = tmp_path / "core"
    for rel in sync_core.apply_manifest():
        dst = core / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((sync_core.LIVE_ROOT / rel).read_bytes())

    assert sync_core.check(core) == []

    doctored = Path("data/ticker_currencies.json")
    assert doctored in sync_core.apply_manifest()
    assert doctored not in sync_core.code_manifest()  # the gap this closes
    (core / doctored).write_text('{"PROBE": "XXX"}', encoding="utf-8")

    assert sync_core.check(core) == [doctored]


def test_the_vendor_registry_is_seeded_but_not_byte_guarded(tmp_path):
    """`data/tickers.json` is regenerated nightly, so it gets the seed contract.

    Layer 2 of currency resolution, but machine-populated: `save_registry` runs
    on every `fetch_ohlcv` invocation and the vendor's name strings are not
    stable ("Crédit Agricole S.A." → "CREDIT AGRICOLE"). Core ships
    `fetch_ohlcv.py`, so a fork regenerates its own on first fetch and the
    seeded copy is a bootstrap, not a contract. A MISSING seed is still drift —
    an absent registry is worse than a stale one.
    """
    core = tmp_path / "core"
    for rel in sync_core.apply_manifest():
        dst = core / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((sync_core.LIVE_ROOT / rel).read_bytes())

    registry = Path("data/tickers.json")
    assert registry in sync_core.apply_manifest(), "apply must still seed it"
    assert registry in sync_core.regenerated_manifest()

    (core / registry).write_text('{"PROBE": {"currency": "XXX"}}', encoding="utf-8")
    assert sync_core.check(core) == [], "content drift must not fail the guard"

    (core / registry).unlink()
    assert sync_core.check(core) == [registry], "a missing seed is still drift"


def test_no_synced_test_imports_a_live_only_script():
    """A test that ships to core must not import a script that does not.

    `LIVE_ONLY_TESTS` is hand-maintained, so the coupling it encodes — "this
    test drives a script core has no copy of" — is only as good as whoever last
    edited the set remembered to be. Every entry is discoverable from the
    imports instead, so discover it: a synced test importing `scripts.foo`
    where `foo.py` is absent from `CORE_SCRIPTS` is an ImportError in the
    public repo's suite, and the live suite is structurally unable to see it.

    Found the real thing on its first run: `test_session_freshness.py` shipped
    while `check_session_freshness.py` (live CI infrastructure, like
    `check_append_only.py`) did not.
    """
    import ast

    synced = {p.name for p in sync_core.code_manifest() if p.parts[0] == "tests"}
    offenders: list[str] = []

    for name in sorted(synced):
        path = sync_core.LIVE_ROOT / "tests" / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "scripts."
            ):
                modules.add(node.module.split(".")[1])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("scripts."):
                        modules.add(alias.name.split(".")[1])
        for module in sorted(modules):
            if f"{module}.py" not in sync_core.CORE_SCRIPTS:
                offenders.append(
                    f"{name} imports scripts.{module}, not in CORE_SCRIPTS"
                )

    assert offenders == [], (
        "these tests would ImportError in midas-core — add them to "
        "LIVE_ONLY_TESTS, or add the script to CORE_SCRIPTS:\n  "
        + "\n  ".join(offenders)
    )
