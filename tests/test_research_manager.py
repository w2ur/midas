"""Research wiring that needs the Manager session harness.

Split from test_research.py because the fixtures live in
test_manager_session.py, which midas-core does not ship: an import of it
from a mirrored test file breaks core's collection.
"""

from __future__ import annotations

import json

import pytest

from tests.test_manager_session import (
    TRADE_DATE,
    _agent_result,
    _seed_ohlcv,
    manager_env,  # noqa: F401  (pytest fixture)
)
from tests.test_research import UNTRUSTED, _src


def test_manager_prompt_carries_block(manager_env) -> None:
    from scripts.daily_session import step_build_manager_prompt

    _seed_ohlcv(manager_env["ohlcv"], "AAPL", "2026-06-01", 200.0)
    prompt = step_build_manager_prompt({"steady-eddie-eur": _agent_result(["AAPL"])}, TRADE_DATE)
    assert "at most 3 calls" in prompt
    assert UNTRUSTED in prompt
    assert f"its {TRADE_DATE.isoformat()} close" in prompt


def test_apply_manager_decision_records_sources(manager_env) -> None:
    from engine.config import get_config
    from scripts.daily_session import step_apply_manager_decision

    raw = {"positions": [], "conviction": 3, "hold_reasoning": "x", "sources": [_src(0)]}
    step_apply_manager_decision(raw, TRADE_DATE)
    path = get_config().research_dir / TRADE_DATE.isoformat() / "the-manager.json"
    assert json.loads(path.read_text())["sources"] == [_src(0)]


@pytest.mark.parametrize(
    "raw",
    [
        {"positions": [], "conviction": 3, "hold_reasoning": "x"},
        {"positions": [], "conviction": 3, "hold_reasoning": "x", "sources": "junk"},
        None,
        "not a dict",
    ],
    ids=["no-sources", "junk-sources", "none", "string"],
)
def test_apply_manager_decision_removes_a_stale_research_file(manager_env, raw) -> None:
    """A reused VM keeps an earlier failed fire's file; nothing reported removes it."""
    from engine.config import get_config
    from scripts.daily_session import step_apply_manager_decision

    path = get_config().research_dir / TRADE_DATE.isoformat() / "the-manager.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n")
    step_apply_manager_decision(raw, TRADE_DATE)
    assert not path.exists()
