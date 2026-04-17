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


@dataclass
class Order:
    """Agent-authored trade request. Validated at construction.

    Long-only invariant: shares must be strictly positive — any attempt at
    short-selling (negative shares) or no-op orders (zero) is rejected.
    """

    order_id: str
    ts: datetime
    agent_id: str
    action: str  # "BUY" | "SELL"
    ticker: str
    shares: float
    reasoning: str
    currency: str

    def __post_init__(self) -> None:
        if not (self.shares > 0):
            raise ValueError(f"Order.shares must be > 0, got {self.shares}")
        if self.action not in ("BUY", "SELL"):
            raise ValueError(
                f"Order.action must be 'BUY' or 'SELL', got {self.action!r}"
            )

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "ts": self.ts.isoformat().replace("+00:00", "Z"),
            "agent_id": self.agent_id,
            "action": self.action,
            "ticker": self.ticker,
            "shares": self.shares,
            "reasoning": self.reasoning,
            "currency": self.currency,
        }

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
        )


@dataclass
class Fill:
    """Paper broker confirmation.

    Status is "filled" or "rejected"; reason set only on rejections.

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

    def __post_init__(self) -> None:
        if self.status not in ("filled", "rejected"):
            raise ValueError(
                f"Fill.status must be 'filled' or 'rejected', got {self.status!r}"
            )

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "ts_filled": self.ts_filled.isoformat().replace("+00:00", "Z"),
            "status": self.status,
            "fill_price": self.fill_price,
            "fill_currency": self.fill_currency,
            "notional_base": self.notional_base,
            "fees": self.fees,
            "reason": self.reason,
        }

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
