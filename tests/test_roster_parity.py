"""roster.yaml must reproduce the legacy cast structures verbatim (default env)."""

import json
from pathlib import Path

import pytest

from engine.config import get_config, reset_config_cache, resolve_agent_universe
from engine.posts import AGENT_DISPLAY_NAMES, AGENT_POST_TIMES, AGENT_VOICE
from engine.baselines import AGENT_BENCHMARKS, DAY_ONE, INITIAL
from scripts.backfill_baselines import AGENT_MAX_POSITIONS, AGENT_UNIVERSES

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    monkeypatch.delenv("MIDAS_DATA_DIR", raising=False)
    reset_config_cache()
    yield
    reset_config_cache()


class TestRosterParity:
    def test_display_names(self):
        cfg = get_config()
        for aid, name in AGENT_DISPLAY_NAMES.items():
            assert cfg.roster[aid].display_name == name

    def test_post_times_and_order(self):
        cfg = get_config()
        assert cfg.trading_roster == tuple(AGENT_POST_TIMES.keys())
        for aid, t in AGENT_POST_TIMES.items():
            assert cfg.roster[aid].post_time == t

    def test_voice(self):
        cfg = get_config()
        for aid, v in AGENT_VOICE.items():
            assert cfg.roster[aid].voice == v

    def test_benchmarks(self):
        cfg = get_config()
        for aid, spec in AGENT_BENCHMARKS.items():
            assert cfg.roster[aid].benchmark.ticker == spec.ticker
            assert cfg.roster[aid].benchmark.currency == spec.currency

    def test_universes_and_max_positions(self):
        cfg = get_config()
        for aid, tickers in AGENT_UNIVERSES.items():
            assert resolve_agent_universe(cfg.roster[aid]) == tickers
            assert cfg.roster[aid].max_positions == AGENT_MAX_POSITIONS[aid]

    def test_globals(self):
        cfg = get_config()
        assert cfg.day_one == DAY_ONE
        assert cfg.initial_capital == INITIAL

    def test_safety_matches_agent_config(self):
        cfg = get_config()
        for aid in AGENT_POST_TIMES:
            path = ROOT / "data" / "agent_config" / f"{aid}.json"
            if not path.exists():
                continue
            raw = json.loads(path.read_text())
            s = cfg.roster[aid].safety
            assert s.max_order_notional == raw["max_order_notional"]
            assert s.daily_drawdown_halt_pct == raw["daily_drawdown_halt_pct"]
            assert list(s.allowed_universe) == raw.get("allowed_universe", [])
