"""Tests for engine.manager_context — TDD (tests written before implementation).

Covers:
- build_manager_context assembles ManagerContext correctly
- snapshot includes both mentioned tickers (from notes) and held tickers (positions)
- unknown ticker (not in price_lookup) → sentinel string, not a fabricated price
- instrument identity (name/type) pulled from ticker registry
- None notes are dropped silently
- empty resolved_decisions → NO outcome memory section in rendered output
- non-empty resolved_decisions → numeric outcome fields present, no thesis/reasoning text
- FEE_TAX_POLICY and RISK_BUDGET constants present in rendered output
- absent portfolio → initial empty book (cash=full capital, no positions)
- Oracle-Fallacy guard: prior reasoning text NEVER appears in the rendered memory block
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from engine.manager_context import (
    FEE_TAX_POLICY,
    RISK_BUDGET,
    SNAPSHOT_TRUTH_INSTRUCTION,
    ManagerContext,
    build_manager_context,
    load_ticker_registry,
    render_manager_context,
)
from engine.research_note import ResearchNote


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict[str, Any] = {
    "initial_capital": 2000.0,
    "currency": "EUR",
}


def _make_note(
    tickers: list[str],
    thesis: str = "Markets look bullish.",
    conviction: int = 7,
    action_bias: str = "buy",
    horizon: str = "weeks",
    catalysts: str = "Strong earnings.",
    currency: str = "EUR",
) -> ResearchNote:
    return ResearchNote(
        thesis=thesis,
        conviction=conviction,
        tickers=tickers,
        action_bias=action_bias,
        horizon=horizon,
        catalysts=catalysts,
        currency=currency,
    )


def _make_portfolio(
    cash: float = 1500.0,
    positions: list[dict] | None = None,
    currency: str = "EUR",
    last_updated: str = "2026-06-12",
) -> dict:
    return {
        "cash": cash,
        "currency": currency,
        "last_updated": last_updated,
        "positions": positions or [],
    }


# ---------------------------------------------------------------------------
# Constants presence
# ---------------------------------------------------------------------------


class TestConstants:
    def test_fee_tax_policy_is_nonempty_string(self) -> None:
        assert isinstance(FEE_TAX_POLICY, str)
        assert len(FEE_TAX_POLICY) > 100

    def test_risk_budget_is_nonempty_string(self) -> None:
        assert isinstance(RISK_BUDGET, str)
        assert len(RISK_BUDGET) > 50

    def test_snapshot_truth_instruction_is_nonempty_string(self) -> None:
        assert isinstance(SNAPSHOT_TRUTH_INSTRUCTION, str)
        assert len(SNAPSHOT_TRUTH_INSTRUCTION) > 20


# ---------------------------------------------------------------------------
# build_manager_context — note assembly
# ---------------------------------------------------------------------------


class TestBuildManagerContextNotes:
    def test_none_notes_are_dropped(self) -> None:
        notes = [("agent-a", None), ("agent-b", _make_note(["AAPL"]))]
        price_lookup = {"AAPL": (150.0, "2026-06-12")}
        registry = {"AAPL": {"name": "Apple Inc.", "type": "equity"}}
        ctx = build_manager_context(
            notes=notes,
            portfolio=_make_portfolio(),
            resolved_decisions=[],
            price_lookup=price_lookup,
            ticker_registry=registry,
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        assert len(ctx.agent_notes) == 1
        assert ctx.agent_notes[0][0] == "agent-b"

    def test_all_none_notes_yields_empty_list(self) -> None:
        ctx = build_manager_context(
            notes=[("agent-a", None), ("agent-b", None)],
            portfolio=_make_portfolio(),
            resolved_decisions=[],
            price_lookup={},
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        assert ctx.agent_notes == []

    def test_agent_id_preserved_in_notes(self) -> None:
        notes = [
            ("satoshi", _make_note(["BTC-EUR"])),
            ("goldfinger", _make_note(["GLD"])),
        ]
        price_lookup = {
            "BTC-EUR": (60000.0, "2026-06-12"),
            "GLD": (180.0, "2026-06-12"),
        }
        registry: dict = {}
        ctx = build_manager_context(
            notes=notes,
            portfolio=_make_portfolio(),
            resolved_decisions=[],
            price_lookup=price_lookup,
            ticker_registry=registry,
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        agent_ids = [a for a, _ in ctx.agent_notes]
        assert "satoshi" in agent_ids
        assert "goldfinger" in agent_ids


# ---------------------------------------------------------------------------
# build_manager_context — snapshot coverage
# ---------------------------------------------------------------------------


class TestBuildManagerContextSnapshot:
    def test_snapshot_includes_mentioned_tickers(self) -> None:
        notes = [("agent-a", _make_note(["BTC-EUR", "ETH-EUR"]))]
        price_lookup = {
            "BTC-EUR": (60000.0, "2026-06-12"),
            "ETH-EUR": (3000.0, "2026-06-12"),
        }
        ctx = build_manager_context(
            notes=notes,
            portfolio=_make_portfolio(),
            resolved_decisions=[],
            price_lookup=price_lookup,
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        snapshot_tickers = {entry["ticker"] for entry in ctx.market_snapshot}
        assert "BTC-EUR" in snapshot_tickers
        assert "ETH-EUR" in snapshot_tickers

    def test_snapshot_includes_held_tickers(self) -> None:
        positions = [
            {
                "ticker": "MSFT",
                "shares": 2.0,
                "avg_cost": 350.0,
                "date_opened": "2026-05-01",
                "grid_level": 0,
            }
        ]
        portfolio = _make_portfolio(positions=positions)
        price_lookup = {"MSFT": (380.0, "2026-06-12")}
        ctx = build_manager_context(
            notes=[],
            portfolio=portfolio,
            resolved_decisions=[],
            price_lookup=price_lookup,
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        snapshot_tickers = {entry["ticker"] for entry in ctx.market_snapshot}
        assert "MSFT" in snapshot_tickers

    def test_snapshot_union_mentioned_and_held(self) -> None:
        notes = [("agent-a", _make_note(["AAPL"]))]
        positions = [
            {
                "ticker": "MSFT",
                "shares": 1.0,
                "avg_cost": 300.0,
                "date_opened": "2026-05-01",
                "grid_level": 0,
            }
        ]
        portfolio = _make_portfolio(positions=positions)
        price_lookup = {
            "AAPL": (150.0, "2026-06-12"),
            "MSFT": (380.0, "2026-06-12"),
        }
        ctx = build_manager_context(
            notes=notes,
            portfolio=portfolio,
            resolved_decisions=[],
            price_lookup=price_lookup,
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        snapshot_tickers = {entry["ticker"] for entry in ctx.market_snapshot}
        assert "AAPL" in snapshot_tickers
        assert "MSFT" in snapshot_tickers

    def test_unknown_ticker_gets_sentinel_not_fabricated_price(self) -> None:
        notes = [("agent-a", _make_note(["UNKNOWN-XYZ"]))]
        ctx = build_manager_context(
            notes=notes,
            portfolio=_make_portfolio(),
            resolved_decisions=[],
            price_lookup={},  # UNKNOWN-XYZ not present
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        snapshot_tickers = {entry["ticker"] for entry in ctx.market_snapshot}
        assert "UNKNOWN-XYZ" in snapshot_tickers
        entry = next(e for e in ctx.market_snapshot if e["ticker"] == "UNKNOWN-XYZ")
        # Must be a sentinel string, not a numeric price
        assert isinstance(entry["close"], str)
        assert "NO_DATA_AVAILABLE" in entry["close"]

    def test_known_ticker_gets_numeric_price(self) -> None:
        notes = [("agent-a", _make_note(["AAPL"]))]
        price_lookup = {"AAPL": (150.0, "2026-06-12")}
        ctx = build_manager_context(
            notes=notes,
            portfolio=_make_portfolio(),
            resolved_decisions=[],
            price_lookup=price_lookup,
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        entry = next(e for e in ctx.market_snapshot if e["ticker"] == "AAPL")
        assert isinstance(entry["close"], float)
        assert entry["close"] == 150.0

    def test_ticker_name_from_registry(self) -> None:
        notes = [("agent-a", _make_note(["AAPL"]))]
        price_lookup = {"AAPL": (150.0, "2026-06-12")}
        registry = {"AAPL": {"name": "Apple Inc.", "type": "equity"}}
        ctx = build_manager_context(
            notes=notes,
            portfolio=_make_portfolio(),
            resolved_decisions=[],
            price_lookup=price_lookup,
            ticker_registry=registry,
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        entry = next(e for e in ctx.market_snapshot if e["ticker"] == "AAPL")
        assert entry["name"] == "Apple Inc."
        assert entry["type"] == "equity"

    def test_ticker_not_in_registry_gives_none_name(self) -> None:
        notes = [("agent-a", _make_note(["AAPL"]))]
        price_lookup = {"AAPL": (150.0, "2026-06-12")}
        ctx = build_manager_context(
            notes=notes,
            portfolio=_make_portfolio(),
            resolved_decisions=[],
            price_lookup={},
            ticker_registry={},  # registry empty
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        entry = next(e for e in ctx.market_snapshot if e["ticker"] == "AAPL")
        assert entry["name"] is None

    def test_snapshot_entry_has_as_of_date(self) -> None:
        notes = [("agent-a", _make_note(["AAPL"]))]
        price_lookup = {"AAPL": (150.0, "2026-06-12")}
        ctx = build_manager_context(
            notes=notes,
            portfolio=_make_portfolio(),
            resolved_decisions=[],
            price_lookup=price_lookup,
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        entry = next(e for e in ctx.market_snapshot if e["ticker"] == "AAPL")
        assert entry["as_of_date"] == "2026-06-12"


# ---------------------------------------------------------------------------
# build_manager_context — portfolio state
# ---------------------------------------------------------------------------


class TestBuildManagerContextPortfolio:
    def test_absent_portfolio_gives_initial_empty_book(self) -> None:
        """None portfolio → initial empty book: cash=initial_capital, no positions."""
        ctx = build_manager_context(
            notes=[],
            portfolio=None,
            resolved_decisions=[],
            price_lookup={},
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config={"initial_capital": 2000.0, "currency": "EUR"},
        )
        assert ctx.portfolio_state["cash"] == 2000.0
        assert ctx.portfolio_state["positions"] == []
        assert ctx.portfolio_state["currency"] == "EUR"

    def test_portfolio_cash_preserved(self) -> None:
        portfolio = _make_portfolio(cash=987.65)
        ctx = build_manager_context(
            notes=[],
            portfolio=portfolio,
            resolved_decisions=[],
            price_lookup={},
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        assert ctx.portfolio_state["cash"] == 987.65

    def test_portfolio_positions_have_current_value(self) -> None:
        positions = [
            {
                "ticker": "BTC-EUR",
                "shares": 0.1,
                "avg_cost": 50000.0,
                "date_opened": "2026-04-17",
                "grid_level": 0,
            }
        ]
        portfolio = _make_portfolio(positions=positions)
        price_lookup = {"BTC-EUR": (60000.0, "2026-06-12")}
        ctx = build_manager_context(
            notes=[],
            portfolio=portfolio,
            resolved_decisions=[],
            price_lookup=price_lookup,
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        pos = ctx.portfolio_state["positions"][0]
        assert pos["ticker"] == "BTC-EUR"
        assert pos["current_value"] == pytest.approx(6000.0)  # 0.1 * 60000

    def test_portfolio_position_has_holding_age(self) -> None:
        positions = [
            {
                "ticker": "ETH-EUR",
                "shares": 1.0,
                "avg_cost": 2000.0,
                "date_opened": "2026-05-01",
                "grid_level": 0,
            }
        ]
        portfolio = _make_portfolio(positions=positions)
        ctx = build_manager_context(
            notes=[],
            portfolio=portfolio,
            resolved_decisions=[],
            price_lookup={},
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        pos = ctx.portfolio_state["positions"][0]
        assert "holding_days" in pos
        # 2026-06-12 - 2026-05-01 = 42 days
        assert pos["holding_days"] == 42


# ---------------------------------------------------------------------------
# build_manager_context — outcome memory
# ---------------------------------------------------------------------------


class TestBuildManagerContextOutcomeMemory:
    def test_empty_resolved_decisions_yields_no_memory(self) -> None:
        ctx = build_manager_context(
            notes=[],
            portfolio=_make_portfolio(),
            resolved_decisions=[],
            price_lookup={},
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        assert ctx.outcome_memory == []

    def test_resolved_decisions_are_filtered_to_last_5_same_ticker_and_3_other(
        self,
    ) -> None:
        positions = [
            {
                "ticker": "BTC-EUR",
                "shares": 0.1,
                "avg_cost": 50000.0,
                "date_opened": "2026-04-17",
                "grid_level": 0,
            }
        ]
        portfolio = _make_portfolio(positions=positions)
        # 6 same-ticker decisions for BTC-EUR (held), 4 other-ticker decisions
        same_ticker = [
            {
                "date": f"2026-0{i + 1}-01",
                "ticker": "BTC-EUR",
                "action": "BUY",
                "realized_return_pct": float(i),
                "alpha_vs_msci_pct": float(i) * 0.5,
                "reasoning": "secret thesis text",
            }
            for i in range(6)
        ]
        other_ticker = [
            {
                "date": f"2026-0{i + 1}-15",
                "ticker": "ETH-EUR",
                "action": "SELL",
                "realized_return_pct": float(-i),
                "alpha_vs_msci_pct": float(-i) * 0.3,
                "reasoning": "another secret reasoning",
            }
            for i in range(4)
        ]
        ctx = build_manager_context(
            notes=[],
            portfolio=portfolio,
            resolved_decisions=same_ticker + other_ticker,
            price_lookup={"BTC-EUR": (60000.0, "2026-06-12")},
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        same = [d for d in ctx.outcome_memory if d["ticker"] == "BTC-EUR"]
        other = [d for d in ctx.outcome_memory if d["ticker"] != "BTC-EUR"]
        assert len(same) <= 5
        assert len(other) <= 3

    def test_outcome_memory_has_no_reasoning_field(self) -> None:
        """Reasoning/thesis MUST NOT appear in outcome memory (Oracle-Fallacy guard)."""
        decisions = [
            {
                "date": "2026-05-01",
                "ticker": "AAPL",
                "action": "BUY",
                "realized_return_pct": 3.5,
                "alpha_vs_msci_pct": 1.2,
                "reasoning": "this is a secret reasoning that must not leak",
            }
        ]
        ctx = build_manager_context(
            notes=[],
            portfolio=_make_portfolio(),
            resolved_decisions=decisions,
            price_lookup={},
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        for entry in ctx.outcome_memory:
            assert "reasoning" not in entry
            assert "thesis" not in entry

    def test_outcome_memory_numeric_fields_present(self) -> None:
        decisions = [
            {
                "date": "2026-05-01",
                "ticker": "AAPL",
                "action": "BUY",
                "realized_return_pct": 3.5,
                "alpha_vs_msci_pct": 1.2,
                "reasoning": "secret text",
            }
        ]
        ctx = build_manager_context(
            notes=[],
            portfolio=_make_portfolio(),
            resolved_decisions=decisions,
            price_lookup={},
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        assert len(ctx.outcome_memory) == 1
        entry = ctx.outcome_memory[0]
        assert entry["realized_return_pct"] == 3.5
        assert entry["alpha_vs_msci_pct"] == 1.2
        assert entry["ticker"] == "AAPL"
        assert entry["action"] == "BUY"
        assert entry["date"] == "2026-05-01"


# ---------------------------------------------------------------------------
# render_manager_context — sections
# ---------------------------------------------------------------------------


class TestRenderManagerContext:
    def _build_basic_ctx(self, resolved_decisions=None) -> ManagerContext:
        notes = [("satoshi", _make_note(["BTC-EUR"]))]
        price_lookup = {"BTC-EUR": (60000.0, "2026-06-12")}
        registry = {"BTC-EUR": {"name": "Bitcoin EUR", "type": "crypto"}}
        return build_manager_context(
            notes=notes,
            portfolio=_make_portfolio(),
            resolved_decisions=resolved_decisions or [],
            price_lookup=price_lookup,
            ticker_registry=registry,
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )

    def test_portfolio_section_present(self) -> None:
        rendered = render_manager_context(self._build_basic_ctx())
        assert "PORTFOLIO" in rendered

    def test_verified_prices_section_present(self) -> None:
        rendered = render_manager_context(self._build_basic_ctx())
        assert "VERIFIED PRICES" in rendered

    def test_analyst_notes_section_present(self) -> None:
        rendered = render_manager_context(self._build_basic_ctx())
        assert "ANALYST NOTES" in rendered

    def test_policy_section_present(self) -> None:
        rendered = render_manager_context(self._build_basic_ctx())
        assert "POLICY" in rendered

    def test_risk_budget_section_present(self) -> None:
        rendered = render_manager_context(self._build_basic_ctx())
        assert "RISK BUDGET" in rendered

    def test_empty_resolved_decisions_no_memory_section(self) -> None:
        rendered = render_manager_context(self._build_basic_ctx(resolved_decisions=[]))
        assert "OUTCOME MEMORY" not in rendered

    def test_non_empty_resolved_decisions_memory_section_present(self) -> None:
        decisions = [
            {
                "date": "2026-05-01",
                "ticker": "BTC-EUR",
                "action": "BUY",
                "realized_return_pct": 3.5,
                "alpha_vs_msci_pct": 1.2,
                "reasoning": "private reasoning text",
            }
        ]
        rendered = render_manager_context(
            self._build_basic_ctx(resolved_decisions=decisions)
        )
        assert "OUTCOME MEMORY" in rendered

    def test_oracle_fallacy_guard_reasoning_not_in_render(self) -> None:
        """The rendered memory block must NEVER contain prior reasoning text."""
        decisions = [
            {
                "date": "2026-05-01",
                "ticker": "BTC-EUR",
                "action": "BUY",
                "realized_return_pct": 3.5,
                "alpha_vs_msci_pct": 1.2,
                "reasoning": "THIS_IS_SECRET_REASONING_MUST_NOT_APPEAR",
            }
        ]
        rendered = render_manager_context(
            self._build_basic_ctx(resolved_decisions=decisions)
        )
        assert "THIS_IS_SECRET_REASONING_MUST_NOT_APPEAR" not in rendered

    def test_fee_tax_policy_content_in_render(self) -> None:
        rendered = render_manager_context(self._build_basic_ctx())
        # The FEE_TAX_POLICY constant content should appear in the render
        assert "PFU" in rendered

    def test_risk_budget_content_in_render(self) -> None:
        rendered = render_manager_context(self._build_basic_ctx())
        assert "conviction" in rendered.lower() or "HOLD" in rendered

    def test_snapshot_truth_instruction_in_render(self) -> None:
        rendered = render_manager_context(self._build_basic_ctx())
        assert (
            "source of truth" in rendered.lower()
            or "SNAPSHOT_TRUTH_INSTRUCTION" in rendered
            or "Treat these prices" in rendered
        )

    def test_no_data_sentinel_in_render_for_unknown_ticker(self) -> None:
        notes = [("agent-x", _make_note(["GHOST-TICKER"]))]
        ctx = build_manager_context(
            notes=notes,
            portfolio=_make_portfolio(),
            resolved_decisions=[],
            price_lookup={},
            ticker_registry={},
            as_of=date(2026, 6, 12),
            config=_DEFAULT_CONFIG,
        )
        rendered = render_manager_context(ctx)
        assert "NO_DATA_AVAILABLE" in rendered

    def test_numeric_outcomes_in_memory_render(self) -> None:
        decisions = [
            {
                "date": "2026-05-01",
                "ticker": "BTC-EUR",
                "action": "BUY",
                "realized_return_pct": 7.89,
                "alpha_vs_msci_pct": 2.34,
                "reasoning": "secret",
            }
        ]
        rendered = render_manager_context(
            self._build_basic_ctx(resolved_decisions=decisions)
        )
        assert "7.89" in rendered
        assert "2.34" in rendered


# ---------------------------------------------------------------------------
# load_ticker_registry
# ---------------------------------------------------------------------------


class TestLoadTickerRegistry:
    def test_loads_from_real_file(self, tmp_path) -> None:
        import json

        data = {
            "AAPL": {"name": "Apple Inc.", "type": "equity"},
            "BTC-EUR": {"name": "Bitcoin EUR", "type": "crypto"},
        }
        reg_file = tmp_path / "tickers.json"
        reg_file.write_text(json.dumps(data))
        registry = load_ticker_registry(path=reg_file)
        assert registry["AAPL"]["name"] == "Apple Inc."
        assert registry["BTC-EUR"]["type"] == "crypto"

    def test_missing_file_returns_empty_dict(self, tmp_path) -> None:
        registry = load_ticker_registry(path=tmp_path / "nonexistent.json")
        assert registry == {}

    def test_malformed_json_returns_empty_dict(self, tmp_path) -> None:
        bad_file = tmp_path / "tickers.json"
        bad_file.write_text("not valid json {{{")
        registry = load_ticker_registry(path=bad_file)
        assert registry == {}
