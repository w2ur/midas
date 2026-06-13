"""Valuation-only refresh: snapshots + baselines + current.json.

Run on weekends (Sat/Sun 20:00 UTC) via .github/workflows/refresh-leaderboard.yml
to keep the live leaderboard widget honest without dispatching agents.

Same idempotent helpers as the weekday session — just without
step_author_orders, step_build_post_prompts, step_save_memories, etc.

Usage:
    python scripts/refresh_leaderboard.py
    python scripts/refresh_leaderboard.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.daily_session import (
    build_portfolio_summaries as _build_portfolio_summaries,
    step_build_baselines as _step_build_baselines,
    step_fetch_market_data as _step_fetch_market_data,
    step_update_snapshots as _step_update_snapshots,
)
from engine.leaderboard import build_leaderboard_rows as _build_leaderboard_rows
from scripts.build_tax_shadow import build_tax_shadow_all as _build_tax_shadow_all

logger = logging.getLogger(__name__)


def _step_build_tax_shadow() -> None:
    """Wrapper that builds tax shadow ledgers using this module's _PROJECT_ROOT.

    Defined locally (not imported from daily_session) so that monkeypatching
    refresh_leaderboard._PROJECT_ROOT during tests redirects output to the
    correct tmp directory — daily_session._PROJECT_ROOT is never read here.
    """
    written = _build_tax_shadow_all(
        portfolios_dir=_PROJECT_ROOT / "data" / "portfolios",
        output_dir=_PROJECT_ROOT / "data" / "tax_shadow",
    )
    logger.info("Tax shadow ledgers written: %d", len(written))


def run(trigger: str, today: date | None = None) -> dict:
    today = today or date.today()
    payload = _step_fetch_market_data()
    _step_update_snapshots(payload)
    _step_build_baselines()
    _step_build_tax_shadow()

    summaries = _build_portfolio_summaries()
    rows = _build_leaderboard_rows(summaries, on=today)

    leaderboard_dir = _PROJECT_ROOT / "data" / "leaderboard"
    leaderboard_dir.mkdir(parents=True, exist_ok=True)
    path = leaderboard_dir / "current.json"
    now_iso = (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    artifact = {"updated_at": now_iso, "trigger": trigger, "rows": rows}
    path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    logger.info("Wrote %s (rows=%d, trigger=%s)", path, len(rows), trigger)
    return artifact


def commit_and_push() -> None:
    """Idempotency contract: full-rewrite of snapshots + baselines + current.json.
    Re-running on the same date is safe; commit is a no-op when nothing changed."""
    paths = [
        str(_PROJECT_ROOT / "data" / "portfolios"),
        str(_PROJECT_ROOT / "data" / "baselines"),
        str(_PROJECT_ROOT / "data" / "leaderboard"),
        str(_PROJECT_ROOT / "data" / "tax_shadow"),
    ]
    subprocess.run(["git", "add", *paths], cwd=_PROJECT_ROOT, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=_PROJECT_ROOT)
    if diff.returncode == 0:
        logger.info("No refresh changes to commit.")
        return
    msg = f"chore: weekend refresh {date.today().isoformat()}"
    subprocess.run(["git", "commit", "-m", msg], cwd=_PROJECT_ROOT, check=True)
    subprocess.run(
        ["git", "push", "origin", "HEAD:main"], cwd=_PROJECT_ROOT, check=True
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description="Valuation-only refresh.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(trigger="scheduled-weekend-refresh")
    if args.dry_run:
        logger.info("Dry-run — skipping commit.")
        return
    commit_and_push()


if __name__ == "__main__":
    main()
