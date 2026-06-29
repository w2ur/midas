"""Manager context builder — assembles the deterministic input block for the LLM Manager.

This module produces a structured, LLM-ready prompt block that the future Manager
(Task C4) will read before authoring real-money orders. It is a PURE ASSEMBLER:
no LLM calls, no network, no side effects beyond reading local files.

Public API
----------
build_manager_context(notes, portfolio, resolved_decisions, price_lookup,
                      ticker_registry, as_of, config) -> ManagerContext
    Pure assembler. All inputs are passed as parameters for testability.

render_manager_context(ctx) -> str
    Renders a ManagerContext to a prompt-injectable string block.

load_ticker_registry(path=None) -> dict[str, dict]
    I/O helper. Reads data/tickers.json; graceful on missing/malformed file.

resolved_decisions schema (C5 contract)
----------------------------------------
Each element of `resolved_decisions` is a dict with these required keys:
    date               : str  — ISO date of the decision (e.g. "2026-05-01")
    ticker             : str  — instrument symbol
    action             : str  — "BUY" | "SELL" | "HOLD"
    realized_return_pct: float — actual realised P&L as a percentage
    alpha_vs_msci_pct  : float — return minus MSCI World benchmark for the period

Optional key (MUST be stripped from outcome memory output — Oracle-Fallacy guard):
    reasoning          : str  — the Manager's prior reasoning; NEVER leaked to render

C5 must produce dicts matching this schema. The Oracle-Fallacy guard is enforced
at both build_manager_context (strip reasoning from outcome_memory entries) and
render_manager_context (render only numeric/identity fields).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from engine.config import get_config
from engine.market_data import no_data_sentinel
from engine.research_note import ResearchNote

if TYPE_CHECKING:
    from engine.orders import Order

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Structured risk limits and PRIIPs blocklist — single source of truth.
# Task D safety rails and the C4 Manager IMPORT these; do NOT re-hardcode.
# ---------------------------------------------------------------------------

RISK_BUDGET_LIMITS: dict = {
    "max_positions": 6,
    "per_position_cap_eur": 400,
    "cash_floor_eur": 150,
    "max_trades_per_week": 2,
    "min_conviction": 6,
}

# US-domiciled leveraged/inverse ETFs blocked for EU retail via IBKR (PRIIPs KID).
# Use UCITS substitutes (3USS.L, QQQS.L) or 1x inverse (SH, PSQ) only.
PRIIPS_BLOCKLIST: frozenset = frozenset(
    {"SQQQ", "SPXS", "SPXU", "TQQQ", "UPRO", "SOXL"}
)

# ---------------------------------------------------------------------------
# Static policy constants (French tax resident, IBIE + Kraken + OANDA)
# Built from structured constants above so prose and machine values cannot diverge.
# ---------------------------------------------------------------------------

_PRIIPS_BLOCKLIST_STR = ", ".join(sorted(PRIIPS_BLOCKLIST))

FEE_TAX_POLICY: str = f"""
FEE AND TAX POLICY (French tax resident — these are GIVEN facts, not suggestions)

TAX
- PFU 30% flat on all realised gains (securities + crypto).
- Securities and crypto are SILOED regimes: losses never cross-subsidise gains in
  the other regime.
- No wash-sale rule in France: you may sell at a loss and immediately re-enter the
  same position.
- Securities losses must be declared the year they occur; 10-year carry-forward.
- Crypto: prefer crypto-to-crypto rebalancing (tax-free sursis under Art. 150 VH bis);
  when de-risking, park in stablecoins (USDC/USDT) — NOT EUR — to defer taxable events.
  Batch any EUR cash-out: each EUR realisation triggers a full-portfolio PVCT snapshot.
- The EUR 305 annual disposal threshold is irrelevant at this scale (blown on first sell).
- PEA ineligible for this universe — all accounts are CTO under full PFU.

FEES (round-trip cost the trade must exceed by ~2x to be worth doing)
- Equity/ETF (IBIE): ~0.05% + EUR 1.25/order floor.
- Crypto (Kraken): 0.40% taker / 0.25% maker.
- FX (OANDA Europe): ~0.002%.

PRIIPS BLOCKLIST — NOT buyable by EU retail via IBKR
These US-domiciled leveraged/inverse ETFs are blocked: {_PRIIPS_BLOCKLIST_STR}. Use UCITS substitutes (3USS.L, QQQS.L) or 1x inverse (SH, PSQ) only.
""".strip()

RISK_BUDGET: str = f"""
RISK BUDGET (hard constraints — DEFAULT ACTION IS HOLD)

- Maximum open positions: {RISK_BUDGET_LIMITS["max_positions"]}
- Per-position cap: ~EUR {RISK_BUDGET_LIMITS["per_position_cap_eur"]} (≈25% of a ~EUR 2,000 book)
- Cash floor: EUR {RISK_BUDGET_LIMITS["cash_floor_eur"]} must remain uninvested at all times
- Turnover limit: ≤{RISK_BUDGET_LIMITS["max_trades_per_week"]} trades per week
- Conviction threshold: conviction < {RISK_BUDGET_LIMITS["min_conviction"]} → do NOT trade; hold cash instead
- When in doubt, HOLD. A missed opportunity costs nothing; a bad trade costs capital.
""".strip()

SNAPSHOT_TRUTH_INSTRUCTION: str = (
    "Treat these prices as the source of truth. "
    "If a research note cites a different price, flag the discrepancy "
    "rather than inventing a reconciled number."
)

# ---------------------------------------------------------------------------
# ManagerContext dataclass
# ---------------------------------------------------------------------------

# Maximum same-ticker and cross-ticker outcome memory entries shown to Manager
_MAX_SAME_TICKER_MEMORY: int = 5
_MAX_OTHER_TICKER_MEMORY: int = 3


@dataclass
class ManagerContext:
    """Assembled input block for the LLM Manager.

    Fields
    ------
    as_of:
        The date for which this context was assembled.
    agent_notes:
        Validated (non-None) research notes: list of (agent_id, ResearchNote).
    market_snapshot:
        One entry per ticker in scope (mentioned + held). Each entry is a dict:
        {ticker, name, type, close (float or sentinel str), as_of_date}.
    portfolio_state:
        {cash, currency, positions: [{ticker, shares, avg_cost, date_opened,
        grid_level, current_value (float|None), holding_days (int)}]}.
    outcome_memory:
        Past resolved decisions with numeric outcome fields only (no reasoning).
        Empty list when no history available.
    config:
        The raw config dict passed by the caller.
    """

    as_of: date
    agent_notes: list[tuple[str, ResearchNote]]
    market_snapshot: list[dict[str, Any]]
    portfolio_state: dict[str, Any]
    outcome_memory: list[dict[str, Any]]
    config: dict[str, Any] = field(default_factory=dict)
    active_triggers: list["Order"] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Ticker registry loader
# ---------------------------------------------------------------------------

_registry_cache: dict[str, dict] | None = None
_registry_cache_path: Path | None = None


def load_ticker_registry(path: Path | None = None) -> dict[str, dict]:
    """Load the ticker name/type registry from disk.

    Parameters
    ----------
    path:
        Path to tickers.json. Defaults to data/tickers.json in the repo root.

    Returns
    -------
    dict[str, dict]
        Map of {symbol: {name, type}}. Returns {} on missing or malformed file.

    Notes
    -----
    Results are module-level cached per path so repeated calls within a session
    are free. Pass an explicit path in tests to bypass the default.
    """
    global _registry_cache, _registry_cache_path
    resolved = path if path is not None else get_config().tickers_path
    if _registry_cache is not None and _registry_cache_path == resolved:
        return _registry_cache

    if not resolved.exists():
        logger.warning(
            "load_ticker_registry: file not found at %s — returning empty registry",
            resolved,
        )
        return {}

    try:
        with resolved.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            logger.warning(
                "load_ticker_registry: expected dict, got %s — returning empty registry",
                type(data).__name__,
            )
            return {}
        _registry_cache = data
        _registry_cache_path = resolved
        return _registry_cache
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "load_ticker_registry: failed to load %s (%s) — returning empty registry",
            resolved,
            exc,
        )
        return {}


# ---------------------------------------------------------------------------
# Pure assembler
# ---------------------------------------------------------------------------


def build_manager_context(
    notes: list[tuple[str, ResearchNote | None]],
    portfolio: dict | None,
    resolved_decisions: list[dict],
    price_lookup: dict[str, tuple[float, str]],
    ticker_registry: dict[str, dict],
    as_of: date,
    config: dict[str, Any],
    active_triggers: "list[Order] | None" = None,
) -> ManagerContext:
    """Assemble a ManagerContext from its constituent inputs.

    This is a pure assembler: deterministic, no LLM, no network calls.

    Parameters
    ----------
    notes:
        List of (agent_id, ResearchNote|None). None notes are dropped silently.
    portfolio:
        Portfolio dict (keys: cash, currency, positions, last_updated) or None.
        None → initial empty book using config["initial_capital"].
    resolved_decisions:
        List of past Manager decisions with known outcomes. See module docstring
        for the required schema. The `reasoning` field (and related fields) are
        stripped before inclusion in outcome_memory (Oracle-Fallacy guard).
    price_lookup:
        {ticker: (close_price, iso_date_string)} for known tickers.
        Tickers absent from this mapping receive the no_data_sentinel string.
    ticker_registry:
        {symbol: {name, type}} instrument identity map. Missing symbols → name=None.
    as_of:
        The reference date for this context.
    config:
        Caller-supplied config dict. Expected keys:
        - initial_capital (float): used when portfolio is None.
        - currency (str): base currency, default "EUR".

    Returns
    -------
    ManagerContext
    """
    # 1. Filter None notes
    valid_notes: list[tuple[str, ResearchNote]] = [
        (agent_id, note) for agent_id, note in notes if note is not None
    ]

    # 2. Collect all tickers in scope: mentioned in notes UNION held positions
    mentioned_tickers: set[str] = set()
    for _, note in valid_notes:
        mentioned_tickers.update(note.tickers)

    held_tickers: set[str] = set()
    if portfolio is not None:
        for pos in portfolio.get("positions", []):
            held_tickers.add(pos["ticker"])

    all_tickers = mentioned_tickers | held_tickers

    # 3. Build market snapshot
    market_snapshot: list[dict[str, Any]] = []
    for ticker in sorted(all_tickers):
        reg_entry = ticker_registry.get(ticker, {})
        name = (reg_entry.get("name") or None) if reg_entry else None
        type_ = (reg_entry.get("type") or None) if reg_entry else None

        if ticker in price_lookup:
            close, as_of_date = price_lookup[ticker]
        else:
            close = no_data_sentinel(ticker)
            as_of_date = as_of.isoformat()

        market_snapshot.append(
            {
                "ticker": ticker,
                "name": name,
                "type": type_,
                "close": close,
                "as_of_date": as_of_date,
            }
        )

    # 4. Build portfolio state
    portfolio_state = _build_portfolio_state(portfolio, price_lookup, as_of, config)

    # 5. Build outcome memory (Oracle-Fallacy guard: strip reasoning fields)
    outcome_memory = _build_outcome_memory(resolved_decisions, held_tickers)

    return ManagerContext(
        as_of=as_of,
        agent_notes=valid_notes,
        market_snapshot=market_snapshot,
        portfolio_state=portfolio_state,
        outcome_memory=outcome_memory,
        config=config,
        active_triggers=list(active_triggers) if active_triggers else [],
    )


def _build_portfolio_state(
    portfolio: dict | None,
    price_lookup: dict[str, tuple[float, str]],
    as_of: date,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Build the portfolio state dict, computing current value and holding age."""
    if portfolio is None:
        return {
            "cash": float(config.get("initial_capital", 2000.0)),
            "currency": config.get("currency", "EUR"),
            "positions": [],
            "last_updated": as_of.isoformat(),
        }

    positions: list[dict[str, Any]] = []
    for pos in portfolio.get("positions", []):
        ticker = pos["ticker"]
        shares = float(pos["shares"])
        avg_cost = float(pos["avg_cost"])
        date_opened_str = pos.get("date_opened")

        # Compute current value if price available
        current_value: float | None = None
        if ticker in price_lookup:
            close, _ = price_lookup[ticker]
            current_value = shares * close

        # Compute holding age
        holding_days: int | None = None
        if date_opened_str:
            try:
                date_opened = date.fromisoformat(date_opened_str)
                holding_days = (as_of - date_opened).days
            except (ValueError, TypeError):
                pass

        positions.append(
            {
                "ticker": ticker,
                "shares": shares,
                "avg_cost": avg_cost,
                "date_opened": date_opened_str,
                "grid_level": pos.get("grid_level", 0),
                "current_value": current_value,
                "holding_days": holding_days,
            }
        )

    return {
        "cash": float(portfolio.get("cash", 0.0)),
        "currency": portfolio.get("currency", "EUR"),
        "positions": positions,
        "last_updated": portfolio.get("last_updated", as_of.isoformat()),
    }


def _build_outcome_memory(
    resolved_decisions: list[dict],
    held_tickers: set[str],
) -> list[dict[str, Any]]:
    """Build the outcome memory list with Oracle-Fallacy guard applied.

    Returns at most _MAX_SAME_TICKER_MEMORY entries for tickers currently held
    and at most _MAX_OTHER_TICKER_MEMORY entries for other tickers.
    All reasoning/thesis fields are stripped — ONLY numeric outcome fields are kept.
    """
    if not resolved_decisions:
        return []

    # Separate into same-ticker (currently held) and cross-ticker decisions.
    # Sort descending by (date, ticker, action) for determinism: same-date entries
    # always appear in the same order regardless of input-list permutation.
    sorted_decisions = sorted(
        resolved_decisions,
        key=lambda d: (d.get("date", ""), d.get("ticker", ""), d.get("action", "")),
        reverse=True,
    )

    same_ticker: list[dict[str, Any]] = []
    other_ticker: list[dict[str, Any]] = []

    for decision in sorted_decisions:
        ticker = decision.get("ticker", "")
        entry = _sanitise_decision(decision)
        if ticker in held_tickers:
            if len(same_ticker) < _MAX_SAME_TICKER_MEMORY:
                same_ticker.append(entry)
        else:
            if len(other_ticker) < _MAX_OTHER_TICKER_MEMORY:
                other_ticker.append(entry)

        if (
            len(same_ticker) >= _MAX_SAME_TICKER_MEMORY
            and len(other_ticker) >= _MAX_OTHER_TICKER_MEMORY
        ):
            break

    return same_ticker + other_ticker


def _sanitise_decision(decision: dict) -> dict[str, Any]:
    """Return a copy of the decision with all reasoning/thesis fields stripped.

    Keeps only: date, ticker, action, realized_return_pct, alpha_vs_msci_pct.
    This is the Oracle-Fallacy guard — prior reasoning must never contaminate
    the Manager's next decision.
    """
    return {
        "date": decision.get("date", ""),
        "ticker": decision.get("ticker", ""),
        "action": decision.get("action", ""),
        "realized_return_pct": decision.get("realized_return_pct"),
        "alpha_vs_msci_pct": decision.get("alpha_vs_msci_pct"),
    }


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def render_manager_context(ctx: ManagerContext) -> str:
    """Render a ManagerContext to a prompt-injectable string block.

    Sections (in order):
    1. PORTFOLIO
    2. VERIFIED PRICES  (includes SNAPSHOT_TRUTH_INSTRUCTION)
    3. ANALYST NOTES
    4. POLICY           (FEE_TAX_POLICY)
    5. RISK BUDGET      (RISK_BUDGET)
    6. OUTCOME MEMORY   (only if ctx.outcome_memory is non-empty)
    """
    parts: list[str] = []

    # ------------------------------------------------------------------
    # Section 1: PORTFOLIO
    # ------------------------------------------------------------------
    ps = ctx.portfolio_state
    portfolio_lines = [
        f"=== PORTFOLIO (as of {ctx.as_of}) ===",
        f"Cash    : {ps['cash']:.2f} {ps['currency']}",
        f"Updated : {ps.get('last_updated', ctx.as_of.isoformat())}",
    ]
    positions = ps.get("positions", [])
    if positions:
        portfolio_lines.append("Positions:")
        for pos in positions:
            cv = (
                f"{pos['current_value']:.2f}"
                if pos.get("current_value") is not None
                else "N/A"
            )
            hd = (
                f"{pos['holding_days']}d"
                if pos.get("holding_days") is not None
                else "N/A"
            )
            portfolio_lines.append(
                f"  {pos['ticker']:<12} {pos['shares']} shares @ avg {pos['avg_cost']:.2f} "
                f"| current value {cv} | held {hd}"
            )
    else:
        portfolio_lines.append("Positions : (none — initial empty book)")
    parts.append("\n".join(portfolio_lines))

    # ------------------------------------------------------------------
    # Section 2: VERIFIED PRICES
    # ------------------------------------------------------------------
    price_lines = [
        f"=== VERIFIED PRICES (as of {ctx.as_of}) ===",
        SNAPSHOT_TRUTH_INSTRUCTION,
        "",
    ]
    for entry in ctx.market_snapshot:
        name_part = f" — {entry['name']}" if entry.get("name") else ""
        type_part = f" [{entry['type']}]" if entry.get("type") else ""
        close = entry["close"]
        if isinstance(close, float):
            close_str = f"{close:.4f}"
        else:
            close_str = str(close)
        price_lines.append(
            f"  {entry['ticker']}{type_part}{name_part}: {close_str} (date: {entry['as_of_date']})"
        )
    if not ctx.market_snapshot:
        price_lines.append("  (no tickers in scope)")
    parts.append("\n".join(price_lines))

    # ------------------------------------------------------------------
    # Section 3: ANALYST NOTES
    # ------------------------------------------------------------------
    notes_lines = [f"=== ANALYST NOTES ({len(ctx.agent_notes)} agents) ==="]
    if ctx.agent_notes:
        for agent_id, note in ctx.agent_notes:
            notes_lines.append(f"\n[{agent_id}]")
            notes_lines.append(f"  Tickers    : {', '.join(note.tickers)}")
            notes_lines.append(
                f"  Bias       : {note.action_bias}  (conviction {note.conviction}/10)"
            )
            notes_lines.append(f"  Horizon    : {note.horizon}")
            notes_lines.append(f"  Thesis     : {note.thesis}")
            notes_lines.append(f"  Catalysts  : {note.catalysts}")
            notes_lines.append(f"  Currency   : {note.currency}")
    else:
        notes_lines.append("(no analyst notes for this session)")
    parts.append("\n".join(notes_lines))

    # ------------------------------------------------------------------
    # Section 4: POLICY
    # ------------------------------------------------------------------
    parts.append(f"=== POLICY ===\n{FEE_TAX_POLICY}")

    # ------------------------------------------------------------------
    # Section 5: RISK BUDGET
    # ------------------------------------------------------------------
    parts.append(f"=== RISK BUDGET ===\n{RISK_BUDGET}")

    # ------------------------------------------------------------------
    # Section 6: OUTCOME MEMORY (only if non-empty — Oracle-Fallacy guard)
    # ------------------------------------------------------------------
    if ctx.outcome_memory:
        memory_lines = [
            "=== OUTCOME MEMORY (numeric outcomes only — no prior reasoning) ==="
        ]
        for entry in ctx.outcome_memory:
            ret = (
                f"{entry['realized_return_pct']:+.2f}%"
                if entry.get("realized_return_pct") is not None
                else "N/A"
            )
            alpha = (
                f"{entry['alpha_vs_msci_pct']:+.2f}%"
                if entry.get("alpha_vs_msci_pct") is not None
                else "N/A"
            )
            memory_lines.append(
                f"  {entry['date']}  {entry['ticker']:<12} {entry['action']:<6} "
                f"return={ret}  alpha={alpha}"
            )
        parts.append("\n".join(memory_lines))

    # ------------------------------------------------------------------
    # Section 7: ACTIVE TRIGGERS (only if non-empty — no section when absent)
    # ------------------------------------------------------------------
    if ctx.active_triggers:
        trigger_lines = [
            "=== ACTIVE TRIGGERS (already parked — do NOT re-author these) ==="
        ]
        for order in ctx.active_triggers:
            trig = order.trigger or {}
            op = trig.get("op", "?")
            level = trig.get("level", "?")
            expires = order.expires or "?"
            trigger_lines.append(
                f"  {order.ticker:<12} {order.action} if {op} {level}"
                f"  |  {order.shares} shares  |  expires {expires}"
            )
        parts.append("\n".join(trigger_lines))

    return "\n\n".join(parts)
