"""The close-runs disclosure names every consequence the committed data shows.

METHODOLOGY `#close-runs-never-deployed-2026-09-28` lists what the missing close
runs cost. Regression: the first version said only market date 2026-09-28 lacked
a snapshot row and called its consequences "all measured", leaving out the
2026-10-01 row that never got written and the market orders that filled at the
previous day's close. The claims are derived here from the committed ledger and
store, so the prose cannot fall behind the data again.

Live-only (see LIVE_ONLY_TESTS in scripts/sync_core.py): core ships neither
METHODOLOGY.md nor the live ledger.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ANCHOR = "close-runs-never-deployed-2026-09-28"
#: Starts 09-29: 2026-09-28 has no row because of the cadence switchover (the
#: 20:00 session published a 09-27 row, the close-run code reached main on 09-29),
#: which no Worker deploy could have changed.
WINDOW = (date(2026, 9, 29), date(2026, 10, 1))
#: First session at 22:00 UTC; the 09-28 session ran at 20:00 and filled at the
#: previous close by the old rule, which is not a consequence of the missing runs.
FILLS_FROM = "2026-09-29"


def _entry() -> str:
    text = (REPO_ROOT / "METHODOLOGY.md").read_text(encoding="utf-8")
    start = text.find(f'<a id="{ANCHOR}"></a>')
    assert start != -1, f"METHODOLOGY has no #{ANCHOR} entry"
    return text[start : text.find("\n- <a id=", start)]


def _weekdays():
    d = WINDOW[0]
    while d <= WINDOW[1]:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def test_every_weekday_without_a_snapshot_row_is_named():
    rows = set()
    for snap in (REPO_ROOT / "data/portfolios").glob("*/snapshots.json"):
        rows |= {r["date"] for r in json.loads(snap.read_text())}
    missing = [d.isoformat() for d in _weekdays() if d.isoformat() not in rows]
    assert missing, "probe must be able to fail: the window has missing rows"
    # The bold summary is what a reader keeps; the date must be in it.
    summary = _entry().split("Nothing published was restated")[0]
    for iso in missing:
        assert iso in summary, f"entry does not name {iso}, which has no snapshot row"


def _close(symbol: str, day: str) -> float | None:
    for line in (REPO_ROOT / f"data/market/ohlcv/{symbol}.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row["date"] == day:
            return row["close"]
    return None


def test_every_previous_close_fill_in_the_window_is_named():
    stale = []
    for inbox in sorted((REPO_ROOT / "data/orders/inbox").glob("*.jsonl")):
        day = inbox.stem
        if not FILLS_FROM <= day <= WINDOW[1].isoformat():
            continue
        orders = {}
        for f in (REPO_ROOT / "data/orders/outbox" / inbox.name).read_text().splitlines():
            o = json.loads(f)
            orders[o["order_id"]] = o
        for line in inbox.read_text().splitlines():
            fill = json.loads(line)
            order = orders.get(fill["order_id"])
            if fill["status"] != "filled" or not order or order.get("trigger"):
                continue
            sym = order["ticker"]
            if "=" in sym or sym.endswith(("-EUR", "-USD")):
                continue  # crypto/FX/futures mark at the previous completed bar by design
            try:
                today, prev = _close(sym, day), None
            except FileNotFoundError:
                continue
            days = [json.loads(x)["date"] for x in (REPO_ROOT / f"data/market/ohlcv/{sym}.jsonl").read_text().splitlines()]
            earlier = [d for d in days if d < day]
            prev = _close(sym, earlier[-1]) if earlier else None
            if today is None or prev is None:
                continue
            if abs(fill["fill_price"] - prev) < 1e-6 and abs(fill["fill_price"] - today) > 1e-6:
                stale.append(fill["order_id"])
    assert stale, "probe must be able to fail: previous-close fills exist in the window"
    entry = _entry()
    for oid in stale:
        assert oid in entry, f"entry does not name {oid}, which filled at the previous close"


def test_switchover_gap_is_not_blamed_on_the_worker():
    """Regression: the headline attributed the 2026-09-28 gap to the undeployed Worker."""
    entry = _entry()
    summary = entry.split("</a>", 1)[1].split("Nothing published was restated")[0]
    assert "2026-09-28" not in summary, "headline blames the Worker for the switchover gap"
    assert "switchover" in entry and "2026-09-28" in entry
