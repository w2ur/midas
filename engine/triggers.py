"""Conditional triggers — pending order storage, cancel I/O, evaluation.

Pending orders live as one JSON file per order at
data/orders/pending/{order_id}.json (easy to add/remove individually, no
concurrency issue at typical volumes — agents author maybe a dozen each).

Cancellations live as append-only JSONL at
data/orders/cancels/YYYY-MM-DD.jsonl (mirrors outbox/inbox shape).

Evaluation and price-fetch dispatch are added in subsequent tasks.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from engine.orders import Order

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
PENDING_DIR = _REPO_ROOT / "data" / "orders" / "pending"
CANCELS_DIR = _REPO_ROOT / "data" / "orders" / "cancels"


@dataclass
class CancelRequest:
    """Agent-authored cancellation of a pending conditional order.

    Recorded in data/orders/cancels/YYYY-MM-DD.jsonl. The broker processes
    these during fill_day: each cancel removes the target from PENDING_DIR
    and writes a rejection-style Fill (reason="CANCELLED_BY_AGENT") to the
    inbox so the next session sees what was cancelled.
    """

    request_id: str
    ts: datetime
    agent_id: str
    target_order_id: str
    reasoning: str

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "ts": self.ts.isoformat().replace("+00:00", "Z"),
            "agent_id": self.agent_id,
            "target_order_id": self.target_order_id,
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CancelRequest":
        return cls(
            request_id=d["request_id"],
            ts=datetime.fromisoformat(d["ts"].replace("Z", "+00:00")),
            agent_id=d["agent_id"],
            target_order_id=d["target_order_id"],
            reasoning=d.get("reasoning", ""),
        )


# ---------- Pending order storage ----------


def save_pending(order: Order) -> None:
    """Persist a conditional order to its per-order JSON file. Overwrites if same order_id."""
    if order.trigger is None:
        raise ValueError(
            f"save_pending requires order.trigger, got None for {order.order_id}"
        )
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    path = PENDING_DIR / f"{order.order_id}.json"
    path.write_text(json.dumps(order.to_dict(), indent=2), encoding="utf-8")


def list_pending() -> list[Order]:
    """Return all pending conditional orders, deterministic by order_id."""
    if not PENDING_DIR.exists():
        return []
    out: list[Order] = []
    for path in sorted(PENDING_DIR.glob("*.json")):
        try:
            out.append(Order.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping malformed pending file %s: %s", path.name, exc)
            continue
    return out


def delete_pending(order_id: str) -> bool:
    """Remove a pending order's file. Returns True if removed, False if absent."""
    path = PENDING_DIR / f"{order_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


# ---------- Cancel request I/O ----------


def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj) + "\n")


def append_cancel(d: date, cancel: CancelRequest) -> None:
    _append_jsonl(CANCELS_DIR / f"{d.isoformat()}.jsonl", cancel.to_dict())


def read_cancels(d: date) -> list[CancelRequest]:
    path = CANCELS_DIR / f"{d.isoformat()}.jsonl"
    if not path.exists():
        return []
    out: list[CancelRequest] = []
    with path.open(encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                out.append(CancelRequest.from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "Skipping malformed cancel line %d in %s: %s", idx, path.name, exc
                )
    return out
