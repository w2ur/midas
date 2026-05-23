"""Leaderboard builders.

Pure functions extracted from scripts/daily_session.step_build_leaderboard
so the same logic powers the weekday session, the weekend refresh cron,
and the in-watcher live update — all anchored to the €10,000 inception
baseline.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from engine.valuation import portfolio_mtm_eur


def build_leaderboard_rows(
    portfolio_summaries: dict[str, dict],
    on: date | None,
) -> list[dict]:
    """Return ranked rows: [{rank, agent, return_pct}, ...] sorted desc.

    Mirrors scripts.daily_session.step_build_leaderboard. Agents whose
    EUR-MTM cannot be computed (e.g. missing FX rate) are dropped.
    """
    rows: list[dict] = []
    for agent_id, summary in portfolio_summaries.items():
        eur_mtm = portfolio_mtm_eur(summary, on)
        if eur_mtm is None:
            continue
        rows.append(
            {
                "agent": agent_id,
                "return_pct": round((eur_mtm / 10_000 - 1) * 100, 4),
            }
        )
    rows.sort(key=lambda r: r["return_pct"], reverse=True)
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


def build_current_leaderboard_artifact(
    portfolio_summaries: dict[str, dict],
    *,
    on: date | None,
    trigger: str,
    updated_at: datetime | None = None,
) -> dict:
    """Build the data/leaderboard/current.json payload."""
    ts = updated_at or datetime.now(timezone.utc)
    iso = (
        ts.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    return {
        "updated_at": iso,
        "trigger": trigger,
        "rows": build_leaderboard_rows(portfolio_summaries, on=on),
    }
