"""Orders — outbox/inbox serde for the Brain/Hands split.

Order: agent-authored trade request (in data/orders/outbox/YYYY-MM-DD.jsonl).
Fill:  paper broker confirmation (in data/orders/inbox/YYYY-MM-DD.jsonl).

Both are append-only JSONL, one record per line. UTC timestamps serialized with
Z suffix for readability; deserialization round-trips cleanly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
OUTBOX_DIR = _REPO_ROOT / "data" / "orders" / "outbox"
INBOX_DIR = _REPO_ROOT / "data" / "orders" / "inbox"

TRIGGER_OPS: tuple[str, ...] = ("<=", ">=")


@dataclass
class Order:
    """Agent-authored trade request. Validated at construction.

    Long-only invariant: shares must be strictly positive — any attempt at
    short-selling (negative shares) or no-op orders (zero) is rejected.

    Optional fields for conditional orders:
      - trigger: {"op": ">="|"<=", "level": float} — fires when current price
        crosses level in the given direction. None → fills immediately end-of-day.
      - expires: ISO date string (YYYY-MM-DD). On or after this date the
        watcher cancels the pending order with reason TRIGGER_EXPIRED.
    """

    order_id: str
    ts: datetime
    agent_id: str
    action: str  # "BUY" | "SELL"
    ticker: str
    shares: float
    reasoning: str
    currency: str
    trigger: dict | None = None
    expires: str | None = None

    def __post_init__(self) -> None:
        if not (self.shares > 0):
            raise ValueError(f"Order.shares must be > 0, got {self.shares}")
        if self.action not in ("BUY", "SELL"):
            raise ValueError(
                f"Order.action must be 'BUY' or 'SELL', got {self.action!r}"
            )
        if self.trigger is not None:
            if not isinstance(self.trigger, dict):
                raise ValueError(
                    f"Order.trigger must be a dict, got {type(self.trigger).__name__}"
                )
            op = self.trigger.get("op")
            if op not in TRIGGER_OPS:
                raise ValueError(
                    f"Order.trigger.op must be one of {TRIGGER_OPS}, got {op!r}"
                )
            level = self.trigger.get("level")
            if not isinstance(level, (int, float)) or isinstance(level, bool):
                raise ValueError("Order.trigger.level must be a number")
        if self.expires is not None and self.trigger is None:
            raise ValueError("Order.expires requires trigger to be set")
        if self.expires is not None:
            try:
                date.fromisoformat(self.expires)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Order.expires must be ISO date YYYY-MM-DD, got {self.expires!r}"
                ) from exc

    def to_dict(self) -> dict:
        d = {
            "order_id": self.order_id,
            "ts": self.ts.isoformat().replace("+00:00", "Z"),
            "agent_id": self.agent_id,
            "action": self.action,
            "ticker": self.ticker,
            "shares": self.shares,
            "reasoning": self.reasoning,
            "currency": self.currency,
        }
        if self.trigger is not None:
            d["trigger"] = self.trigger
        if self.expires is not None:
            d["expires"] = self.expires
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Order":
        return cls(
            order_id=d["order_id"],
            ts=datetime.fromisoformat(d["ts"].replace("Z", "+00:00")),
            agent_id=d["agent_id"],
            action=d["action"],
            ticker=d["ticker"],
            shares=float(d["shares"]),
            reasoning=d.get("reasoning", ""),
            currency=d["currency"],
            trigger=d.get("trigger"),
            expires=d.get("expires"),
        )


@dataclass
class Fill:
    """Paper broker confirmation.

    Status is "filled" or "rejected"; reason set only on rejections.
    trigger_fired: True when the fill came from a conditional order whose
      trigger condition was hit by the watcher (not a same-session market fill).

    Currency convention (filled orders):
      - fill_price, fill_currency — the ticker's NATIVE currency (e.g., MSFT → USD)
      - notional_base             — the agent's BASE currency (post-FX conversion)
    This asymmetry means a USD ticker bought by an EUR agent produces:
        fill_price=400.0, fill_currency="USD", notional_base=360.0  (EUR-equivalent).
    The `_base` suffix is explicit so downstream consumers never confuse the
    two — critical for audit trails and tax reporting later.
    """

    order_id: str
    ts_filled: datetime
    status: str  # "filled" | "rejected"
    fill_price: float | None
    fill_currency: str | None
    notional_base: float | None
    fees: float | None
    reason: str | None
    trigger_fired: bool = False

    def __post_init__(self) -> None:
        if self.status not in ("filled", "rejected"):
            raise ValueError(
                f"Fill.status must be 'filled' or 'rejected', got {self.status!r}"
            )

    def to_dict(self) -> dict:
        d = {
            "order_id": self.order_id,
            "ts_filled": self.ts_filled.isoformat().replace("+00:00", "Z"),
            "status": self.status,
            "fill_price": self.fill_price,
            "fill_currency": self.fill_currency,
            "notional_base": self.notional_base,
            "fees": self.fees,
            "reason": self.reason,
        }
        if self.trigger_fired:
            d["trigger_fired"] = True
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Fill":
        return cls(
            order_id=d["order_id"],
            ts_filled=datetime.fromisoformat(d["ts_filled"].replace("Z", "+00:00")),
            status=d["status"],
            fill_price=d.get("fill_price"),
            fill_currency=d.get("fill_currency"),
            notional_base=d.get("notional_base"),
            fees=d.get("fees"),
            reason=d.get("reason"),
            trigger_fired=bool(d.get("trigger_fired", False)),
        )


def make_order_id(d: date, agent_id: str, seq: int) -> str:
    """Deterministic order ID: ord_{iso_date}_{agent_id}_{seq:03d}."""
    return f"ord_{d.isoformat()}_{agent_id}_{seq:03d}"


def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        try:
            return [json.loads(line) for line in f if line.strip()]
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed JSON in {path}: {exc}") from exc


def append_order(d: date, order: Order) -> None:
    _append_jsonl(OUTBOX_DIR / f"{d.isoformat()}.jsonl", order.to_dict())


def read_outbox(d: date) -> list[Order]:
    return [
        Order.from_dict(r) for r in _read_jsonl(OUTBOX_DIR / f"{d.isoformat()}.jsonl")
    ]


def append_fill(d: date, fill: Fill) -> None:
    _append_jsonl(INBOX_DIR / f"{d.isoformat()}.jsonl", fill.to_dict())


def read_inbox(d: date) -> list[Fill]:
    return [
        Fill.from_dict(r) for r in _read_jsonl(INBOX_DIR / f"{d.isoformat()}.jsonl")
    ]
