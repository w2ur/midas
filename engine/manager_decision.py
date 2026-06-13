"""Manager decision — structured output from the LLM Manager for real-money orders.

The Manager emits a ManagerDecision after reading the manager context (C3). This
module defines the typed schema, the tolerant parser (safe for agent output), and
the conviction gate — enforced in code so a low-conviction decision CANNOT carry
orders regardless of what the LLM emits.

Conviction gate contract
------------------------
parse_manager_decision() is the ONLY entry point for agent output. After
assembling a valid decision it checks conviction against
RISK_BUDGET_LIMITS["min_conviction"]. If conviction < threshold:
  - positions is forced to [] regardless of what the LLM returned.
  - hold_reasoning is synthesised if the model left it blank.

This is a Hands-side rail (see CLAUDE.md Brain/Hands principle): the persona
prompt also explains the gate, but enforcement lives here, not in the prompt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from engine.manager_context import RISK_BUDGET_LIMITS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical value set
# ---------------------------------------------------------------------------

ACTION_VALUES: frozenset[str] = frozenset({"BUY", "SELL", "HOLD"})


# ---------------------------------------------------------------------------
# ManagerPosition dataclass
# ---------------------------------------------------------------------------


@dataclass
class ManagerPosition:
    """A single position directive in a ManagerDecision.

    Fields
    ------
    ticker:
        Non-empty instrument symbol (e.g. "BTC-EUR", "AAPL").
    action:
        One of "BUY", "SELL", "HOLD".
    size_eur:
        Integer EUR amount for the trade. 0 is allowed (HOLD / SELL full position).
        NOT shares — NOT a percentage.
    entry_guidance:
        Optional free-text guidance for order placement (may be empty string).
    stop_loss:
        Optional stop-loss price in the instrument's quote currency. None if not set.
    reasoning:
        Non-empty rationale (project rule: no silent trades).
    """

    ticker: str
    action: str
    size_eur: int
    entry_guidance: str
    stop_loss: float | None
    reasoning: str

    def __post_init__(self) -> None:
        if not self.ticker:
            raise ValueError("ManagerPosition.ticker must be a non-empty string")
        if self.action not in ACTION_VALUES:
            raise ValueError(
                f"ManagerPosition.action must be one of {sorted(ACTION_VALUES)}, "
                f"got {self.action!r}"
            )
        if type(self.size_eur) is not int:
            raise ValueError(
                f"ManagerPosition.size_eur must be an int, got {type(self.size_eur).__name__!r}"
            )
        if self.size_eur < 0:
            raise ValueError(
                f"ManagerPosition.size_eur must be non-negative, got {self.size_eur}"
            )
        if not self.reasoning:
            raise ValueError("ManagerPosition.reasoning must be a non-empty string")

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dictionary."""
        return {
            "ticker": self.ticker,
            "action": self.action,
            "size_eur": self.size_eur,
            "entry_guidance": self.entry_guidance,
            "stop_loss": self.stop_loss,
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ManagerPosition":
        """Deserialize from a dictionary. Validates strictly (raises on bad data)."""
        return cls(
            ticker=str(d["ticker"]),
            action=str(d["action"]),
            size_eur=int(d["size_eur"]),
            entry_guidance=str(d.get("entry_guidance", "")),
            stop_loss=float(d["stop_loss"]) if d.get("stop_loss") is not None else None,
            reasoning=str(d["reasoning"]),
        )


# ---------------------------------------------------------------------------
# ManagerDecision dataclass
# ---------------------------------------------------------------------------


@dataclass
class ManagerDecision:
    """The Manager's complete decision for one session.

    Fields
    ------
    positions:
        List of ManagerPosition directives. Empty list means hold everything.
    conviction:
        Overall Manager conviction 0 (no conviction) to 10 (maximum).
        If below RISK_BUDGET_LIMITS["min_conviction"], positions MUST be empty
        (enforced by parse_manager_decision — see conviction gate).
    hold_reasoning:
        Explanation for holding / not trading. Must be populated when positions
        is empty and conviction is below the gate threshold.
    """

    positions: list[ManagerPosition]
    conviction: int
    hold_reasoning: str

    def __post_init__(self) -> None:
        if type(self.conviction) is not int or not (0 <= self.conviction <= 10):
            raise ValueError(
                f"ManagerDecision.conviction must be an int 0-10, got {self.conviction!r}"
            )

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dictionary."""
        return {
            "positions": [p.to_dict() for p in self.positions],
            "conviction": self.conviction,
            "hold_reasoning": self.hold_reasoning,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ManagerDecision":
        """Deserialize from a dictionary. Validates strictly (raises on bad data)."""
        positions = [ManagerPosition.from_dict(p) for p in d.get("positions", [])]
        return cls(
            positions=positions,
            conviction=int(d["conviction"]),
            hold_reasoning=str(d.get("hold_reasoning", "")),
        )


# ---------------------------------------------------------------------------
# Tolerant constructor for agent output (with conviction gate)
# ---------------------------------------------------------------------------


def _parse_position(raw: object) -> ManagerPosition | None:
    """Tolerant parse of a single position dict from agent output.

    Returns None (and logs a warning) on any validation failure, rather than
    raising. The caller drops the None and continues with remaining positions.
    """
    if not isinstance(raw, dict):
        logger.warning(
            "parse_manager_decision: position is not a dict (%s) — dropping",
            type(raw).__name__,
        )
        return None

    ticker = str(raw.get("ticker") or "")
    if not ticker:
        logger.warning("parse_manager_decision: position missing ticker — dropping")
        return None

    action = raw.get("action")
    if action not in ACTION_VALUES:
        logger.warning(
            "parse_manager_decision: invalid action %r (expected one of %s) — dropping position",
            action,
            sorted(ACTION_VALUES),
        )
        return None

    # size_eur must be a non-negative integer. Accept int-like floats (e.g. 300.0 → 300).
    raw_size = raw.get("size_eur", 0)
    try:
        size_eur = int(raw_size)
        if size_eur != float(raw_size):
            logger.warning(
                "parse_manager_decision: size_eur %r is not an integer value — dropping position",
                raw_size,
            )
            return None
    except (TypeError, ValueError):
        logger.warning(
            "parse_manager_decision: cannot convert size_eur %r to int — dropping position",
            raw_size,
        )
        return None

    if size_eur < 0:
        logger.warning(
            "parse_manager_decision: negative size_eur %d — dropping position", size_eur
        )
        return None

    reasoning = str(raw.get("reasoning") or "")
    if not reasoning:
        logger.warning(
            "parse_manager_decision: position for %s has empty reasoning — dropping",
            ticker,
        )
        return None

    # stop_loss is optional; coerce to float or None
    stop_loss: float | None = None
    raw_sl = raw.get("stop_loss")
    if raw_sl is not None:
        try:
            stop_loss = float(raw_sl)
        except (TypeError, ValueError):
            logger.warning(
                "parse_manager_decision: cannot parse stop_loss %r for %s — setting None",
                raw_sl,
                ticker,
            )

    entry_guidance = str(raw.get("entry_guidance") or "")

    try:
        return ManagerPosition(
            ticker=ticker,
            action=str(action),
            size_eur=size_eur,
            entry_guidance=entry_guidance,
            stop_loss=stop_loss,
            reasoning=reasoning,
        )
    except (ValueError, TypeError) as exc:
        logger.warning(
            "parse_manager_decision: ManagerPosition construction failed (%s) — dropping",
            exc,
        )
        return None


def parse_manager_decision(raw: dict | None) -> ManagerDecision | None:
    """Tolerant constructor for the Manager's emitted JSON.

    Contract
    --------
    - None, empty dict, or non-dict → None.
    - Missing / unrecoverable fields → None + warning logged.
    - conviction out of range → clamped to [0, 10].
    - Non-numeric conviction → defaults to 0 (conservative).
    - Individual malformed positions → dropped; remaining positions kept.
    - CONVICTION GATE: if conviction < RISK_BUDGET_LIMITS["min_conviction"],
      positions is forced to [] and hold_reasoning is synthesised if blank.
      This is enforced HERE in code, not just in the prompt.

    This method NEVER raises. A bad response degrades gracefully.
    """
    if not raw:
        return None

    if not isinstance(raw, dict):
        logger.warning(
            "parse_manager_decision: expected dict, got %s — returning None",
            type(raw).__name__,
        )
        return None

    # --- Parse conviction (clamp; default to 0 on failure) ---
    try:
        conviction = int(raw.get("conviction", 0))
    except (TypeError, ValueError):
        logger.warning(
            "parse_manager_decision: non-numeric conviction %r — defaulting to 0",
            raw.get("conviction"),
        )
        conviction = 0
    conviction = max(0, min(10, conviction))

    # --- Parse positions (tolerant; drop invalid items) ---
    raw_positions = raw.get("positions", [])
    positions: list[ManagerPosition] = []
    if isinstance(raw_positions, list):
        for item in raw_positions:
            pos = _parse_position(item)
            if pos is not None:
                positions.append(pos)
    else:
        logger.warning(
            "parse_manager_decision: 'positions' is not a list (%s) — treating as empty",
            type(raw_positions).__name__,
        )

    hold_reasoning = str(raw.get("hold_reasoning") or "")

    # --- CONVICTION GATE (Hands-side rail) ---
    min_conviction: int = RISK_BUDGET_LIMITS["min_conviction"]
    if conviction < min_conviction:
        if positions:
            logger.warning(
                "parse_manager_decision: conviction %d < threshold %d — "
                "dropping %d position(s) (conviction gate)",
                conviction,
                min_conviction,
                len(positions),
            )
        positions = []
        if not hold_reasoning:
            hold_reasoning = (
                f"Conviction {conviction} below threshold {min_conviction} — holding."
            )

    try:
        return ManagerDecision(
            positions=positions,
            conviction=conviction,
            hold_reasoning=hold_reasoning,
        )
    except (ValueError, TypeError) as exc:
        logger.warning(
            "parse_manager_decision: ManagerDecision construction failed (%s) — returning None",
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_hold(decision: ManagerDecision) -> bool:
    """True when the decision carries no active orders.

    A decision is a hold when it has no positions OR all positions have
    action == "HOLD" (explicit hold signals rather than absence).
    """
    return not decision.positions or all(p.action == "HOLD" for p in decision.positions)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_manager_decision(decision: ManagerDecision) -> str:
    """Compact human-readable rendering of a ManagerDecision.

    Intended for audit logs, the C5 manager-review artifact, and Oracle context.
    Single-block output, no trailing newline.
    """
    lines = [
        "[Manager Decision]",
        f"  conviction: {decision.conviction}/10",
    ]

    if decision.positions:
        lines.append(f"  positions ({len(decision.positions)}):")
        for pos in decision.positions:
            sl_str = f"{pos.stop_loss}" if pos.stop_loss is not None else "none"
            lines.append(
                f"    {pos.ticker:<14} {pos.action:<4} EUR {pos.size_eur}"
                f"  stop={sl_str}"
            )
            if pos.entry_guidance:
                lines.append(f"      entry:   {pos.entry_guidance}")
            lines.append(f"      reason:  {pos.reasoning}")
    else:
        lines.append(f"  hold_reasoning: {decision.hold_reasoning or '(none)'}")

    return "\n".join(lines)
