"""Runtime configuration — single source of truth for paths, globals, and the roster.

Replaces the former module-level ``Path(__file__).resolve().parents[1]`` data-root
assumptions (which break once the package is pip-installed) and the hardcoded cast
dicts scattered across engine.posts / engine.baselines / scripts.backfill_baselines.

The project root is resolved from ``MIDAS_DATA_DIR`` (default: the repo root two
levels up — legacy behaviour). The cast + globals load from ``<root>/roster.yaml``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

_LEGACY_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class BenchmarkSpec:
    label: str
    ticker: str
    currency: str


@dataclass(frozen=True)
class SafetyRails:
    max_order_notional: float = 500.0
    max_orders_per_day: int = 100
    daily_drawdown_halt_pct: float = -5.0
    allowed_universe: tuple[str, ...] = ()
    dry_run: bool = False


@dataclass(frozen=True)
class AgentSpec:
    id: str
    display_name: str
    voice: str
    post_time: str
    home_currency: str
    initial_capital: float
    max_positions: int
    universe: str | list[str] | None  # universe name, list of names, or unset
    benchmark: BenchmarkSpec | None
    persona: str
    role: str = "trader"
    safety: SafetyRails = field(default_factory=SafetyRails)


@dataclass(frozen=True)
class MidasConfig:
    data_dir: Path
    day_one: date
    currencies: tuple[str, ...]
    initial_capital: float
    global_reference: BenchmarkSpec
    agents_dir: Path
    roster: dict[str, AgentSpec]

    @property
    def _data(self) -> Path:
        return self.data_dir / "data"

    @property
    def posts_dir(self) -> Path:
        return self._data / "posts"

    @property
    def baselines_dir(self) -> Path:
        return self._data / "baselines"

    @property
    def ohlcv_dir(self) -> Path:
        return self._data / "market" / "ohlcv"

    @property
    def journal_dir(self) -> Path:
        return self._data / "agent_memory"

    @property
    def logs_dir(self) -> Path:
        return self._data / "logs"

    @property
    def blog_dir(self) -> Path:
        return self._data / "blog"

    @property
    def output_dir(self) -> Path:
        return self._data / "output"

    @property
    def universes_dir(self) -> Path:
        return self._data / "universes"

    @property
    def agent_config_dir(self) -> Path:
        return self._data / "agent_config"

    @property
    def orders_dir(self) -> Path:
        return self._data / "orders"

    @property
    def tickers_path(self) -> Path:
        return self._data / "tickers.json"

    @property
    def trading_roster(self) -> tuple[str, ...]:
        return tuple(aid for aid, spec in self.roster.items() if spec.role == "trader")


def _benchmark(raw: dict | None) -> BenchmarkSpec | None:
    if not raw:
        return None
    return BenchmarkSpec(
        label=raw["label"], ticker=raw["ticker"], currency=raw["currency"]
    )


def _safety(raw: dict | None) -> SafetyRails:
    raw = raw or {}
    return SafetyRails(
        max_order_notional=float(raw.get("max_order_notional", 500.0)),
        max_orders_per_day=int(raw.get("max_orders_per_day", 100)),
        daily_drawdown_halt_pct=float(raw.get("daily_drawdown_halt_pct", -5.0)),
        allowed_universe=tuple(raw.get("allowed_universe", []) or []),
        dry_run=bool(raw.get("dry_run", False)),
    )


def _agent(agent_id: str, raw: dict, default_capital: float) -> AgentSpec:
    return AgentSpec(
        id=agent_id,
        display_name=raw["display_name"],
        voice=raw.get("voice", ""),
        post_time=raw.get("post_time", ""),
        home_currency=raw.get("home_currency", "EUR"),
        initial_capital=float(raw.get("initial_capital", default_capital)),
        max_positions=int(raw.get("max_positions", 5)),
        universe=raw.get("universe"),
        benchmark=_benchmark(raw.get("benchmark")),
        persona=raw.get("persona", f"{agent_id}.md"),
        role=raw.get("role", "trader"),
        safety=_safety(raw.get("safety")),
    )


def _resolve_data_dir() -> Path:
    env = os.environ.get("MIDAS_DATA_DIR")
    return Path(env).expanduser().resolve() if env else _LEGACY_ROOT


def _load(data_dir: Path) -> MidasConfig:
    raw = yaml.safe_load((data_dir / "roster.yaml").read_text(encoding="utf-8"))
    g = raw["globals"]
    day_one = g["day_one"]
    if not isinstance(day_one, date):
        day_one = date.fromisoformat(str(day_one))
    default_capital = float(g.get("initial_capital", 10000.0))
    roster = {aid: _agent(aid, a, default_capital) for aid, a in raw["agents"].items()}
    return MidasConfig(
        data_dir=data_dir,
        day_one=day_one,
        currencies=tuple(g.get("currencies", ["EUR", "USD"])),
        initial_capital=default_capital,
        global_reference=_benchmark(g["global_reference"]),
        agents_dir=data_dir / g.get("agents_dir", ".claude/agents"),
        roster=roster,
    )


@lru_cache(maxsize=1)
def get_config() -> MidasConfig:
    return _load(_resolve_data_dir())


def reset_config_cache() -> None:
    """Clear the cached config — for tests that change MIDAS_DATA_DIR between cases."""
    get_config.cache_clear()


def resolve_agent_universe(spec: AgentSpec) -> list[str]:
    """Resolve an agent's universe (a name or list of names) to a ticker list.

    ``spec.universe`` is a single universe name (str) or a list of names
    (list[str]), each resolved via engine.universes.resolve_universe. The
    composition rules replicate today's behaviour in scripts.backfill_baselines
    verbatim:
      - empty / None       -> []
      - exactly one name   -> resolve_universe(name) (native order, NOT sorted)
      - two or more names  -> sorted({t for n in names for t in resolve_universe(n)})
    """
    names = spec.universe
    if not names:
        return []
    if isinstance(names, str):
        names = [names]
    from engine.universes import resolve_universe

    if len(names) == 1:
        return resolve_universe(names[0])
    return sorted({ticker for name in names for ticker in resolve_universe(name)})
