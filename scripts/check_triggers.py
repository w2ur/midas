"""Conditional-order watcher.

Runs every 15 min via .github/workflows/check-triggers.yml. Walks pending
orders, fetches current prices, fires when triggers are hit, expires old
ones. Blackout window 19:55-20:30 UTC to avoid commit-races with the
20:00 UTC daily session.

Usage:
    python scripts/check_triggers.py            # normal run
    python scripts/check_triggers.py --dry-run  # evaluate but don't commit
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from engine.orders import Fill, append_fill
from engine.paper_broker import execute_triggered_order
from engine.portfolio import PortfolioManager
from engine.triggers import (
    delete_pending,
    evaluate_trigger,
    get_current_price,
    is_expired,
    list_pending,
)
from engine.leaderboard import build_leaderboard_rows as _build_leaderboard_rows
from scripts.daily_session import (
    build_portfolio_summaries as _build_portfolio_summaries,
)

logger = logging.getLogger(__name__)

BLACKOUT_START = time(19, 55)
BLACKOUT_END = time(20, 30)


def in_blackout(now: datetime) -> bool:
    """True if `now` (UTC) is inside the daily-session blackout window."""
    t = now.time()
    return BLACKOUT_START <= t <= BLACKOUT_END


def run(now: datetime, portfolio_manager: PortfolioManager | None) -> dict:
    """Process all pending orders. Returns a summary dict for logging.

    portfolio_manager may be None ONLY during blackout (we short-circuit before use).
    """
    # Late binding: tests monkeypatch `engine.triggers.get_current_price` so we
    # must call it through the module attribute, not the imported name.
    from engine import triggers as _triggers

    summary = {
        "blacked_out": False,
        "checked": 0,
        "fired": 0,
        "expired": 0,
        "carried": 0,
        "errors": 0,
    }
    if in_blackout(now):
        summary["blacked_out"] = True
        logger.info("In blackout window %s — skipping.", now.time().isoformat())
        return summary

    today = now.date()
    pending = list_pending()
    summary["checked"] = len(pending)

    for order in pending:
        if is_expired(order, today):
            f = Fill(
                order_id=order.order_id,
                ts_filled=now,
                status="rejected",
                fill_price=None,
                fill_currency=None,
                notional_base=None,
                fees=None,
                reason="TRIGGER_EXPIRED",
                trigger_fired=True,
            )
            append_fill(today, f)
            delete_pending(order.order_id)
            summary["expired"] += 1
            continue

        price = _triggers.get_current_price(order.ticker, today=today)
        if price is None:
            summary["carried"] += 1
            continue
        if not evaluate_trigger(price, order.trigger):
            summary["carried"] += 1
            continue

        # Trigger hit — execute through the broker safety rails.
        try:
            f = execute_triggered_order(
                order, today, portfolio_manager, fire_price=price
            )
        except Exception as exc:
            logger.exception(
                "execute_triggered_order failed for %s: %s", order.order_id, exc
            )
            summary["errors"] += 1
            continue

        append_fill(today, f)
        # Remove pending regardless of fill/reject — the rejection is the final word
        # and we don't want indefinite retries. Agent can re-author next session.
        delete_pending(order.order_id)
        summary["fired"] += 1

    return summary


def refresh_leaderboard_artifact(trigger: str, on: date) -> None:
    """Best-effort refresh of data/leaderboard/current.json after a fire.

    Wrapped: any failure here is logged but never raised. The fill is the
    critical bit; the leaderboard is derived state and resyncs on the next
    fire / weekend refresh / weekday session.
    """
    try:
        summaries = _build_portfolio_summaries()
        rows = _build_leaderboard_rows(summaries, on=on)
        leaderboard_dir = _PROJECT_ROOT / "data" / "leaderboard"
        leaderboard_dir.mkdir(parents=True, exist_ok=True)
        path = leaderboard_dir / "current.json"
        now_iso = (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        artifact = {"updated_at": now_iso, "trigger": trigger, "rows": rows}
        path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
        logger.info("Refreshed %s after fire (rows=%d)", path, len(rows))
    except Exception as exc:
        logger.warning("Leaderboard refresh failed (non-fatal): %s", exc)


def commit_and_push() -> None:
    """Commit data/orders/{pending,inbox}/ and data/portfolios/ and data/leaderboard/ changes and push to origin/main."""
    data_dirs = [
        str(_PROJECT_ROOT / "data" / "orders" / "pending"),
        str(_PROJECT_ROOT / "data" / "orders" / "inbox"),
        str(_PROJECT_ROOT / "data" / "portfolios"),
        str(_PROJECT_ROOT / "data" / "leaderboard"),
    ]
    subprocess.run(["git", "add", *data_dirs], cwd=_PROJECT_ROOT, check=True)
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=_PROJECT_ROOT,
    )
    if diff.returncode == 0:
        logger.info("No trigger changes to commit.")
        return
    msg = (
        f"chore(triggers): execute fired/expired conditions {date.today().isoformat()}"
    )
    subprocess.run(["git", "commit", "-m", msg], cwd=_PROJECT_ROOT, check=True)
    subprocess.run(
        ["git", "push", "origin", "HEAD:main"], cwd=_PROJECT_ROOT, check=True
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description="Conditional-order watcher.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Evaluate but don't commit."
    )
    args = parser.parse_args()

    portfolio_manager = PortfolioManager(base_dir=_PROJECT_ROOT / "data" / "portfolios")
    now = datetime.now(timezone.utc)
    summary = run(now=now, portfolio_manager=portfolio_manager)
    logger.info("Watcher summary: %s", summary)

    if args.dry_run:
        logger.info("Dry-run — skipping commit.")
        return
    if summary["blacked_out"]:
        return
    if summary["fired"] == 0 and summary["expired"] == 0:
        return
    if summary["fired"] > 0:
        refresh_leaderboard_artifact(trigger="trigger-fire", on=now.date())
    commit_and_push()


if __name__ == "__main__":
    main()
