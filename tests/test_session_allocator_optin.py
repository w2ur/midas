"""Opt-in allocator wiring — a roster with no allocator runs all four manager
session steps as clean skips, writing ZERO manager artifacts (SP2 Task 5).

The single shipped allocator (`the-manager`) is covered for byte-identical parity
by tests/test_manager_session.py + tests/test_manager_context_golden.py. This file
proves the OTHER end of the spectrum: a forker who omits the allocator block from
roster.yaml gets a functioning session that simply never touches the manager
channel, book, or baseline twin.
"""

from __future__ import annotations

from datetime import date

import pytest

from engine.config import reset_config_cache


@pytest.fixture
def no_allocator_root(tmp_path, monkeypatch):
    roster = tmp_path / "roster.yaml"
    roster.write_text(
        "globals:\n  day_one: '2026-04-17'\n  currencies: [USD]\n"
        "  initial_capital: 10000.0\n"
        "  global_reference: {label: X, ticker: URTH, currency: USD}\n"
        "  agents_dir: .claude/agents\n"
        "agents:\n  solo:\n    display_name: Solo\n    role: trader\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MIDAS_DATA_DIR", str(tmp_path))
    reset_config_cache()
    yield tmp_path
    reset_config_cache()


def test_build_manager_prompt_skips_when_no_allocator(no_allocator_root):
    from scripts.daily_session import step_build_manager_prompt

    assert step_build_manager_prompt({}, date(2026, 7, 1)) == ""


def test_apply_manager_decision_skips_when_no_allocator(no_allocator_root):
    from scripts.daily_session import step_apply_manager_decision

    # Should not raise and should write no manager-outbox
    step_apply_manager_decision({"conviction": 9, "positions": []}, date(2026, 7, 1))
    assert not (no_allocator_root / "data" / "orders" / "manager-outbox").exists()


def test_build_baseline_manager_skips_when_disabled(no_allocator_root):
    from scripts.daily_session import step_build_baseline_manager

    step_build_baseline_manager({}, date(2026, 7, 1))
    assert not (no_allocator_root / "data" / "portfolios" / "baseline-manager").exists()
