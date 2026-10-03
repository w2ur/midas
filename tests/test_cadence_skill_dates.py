"""The cadence skill dates the weekday cron by when it took its current value.

Regression: the skill said `0 22 * * 1-5` held "since 2026-09-28" while its own
parenthetical said the cron was `0-4` from 2026-09-29 and became `1-5` only on
2026-10-03. The current value's date is the one in the trigger prose, so this
holds the skill to the METHODOLOGY entry that records the change.

Live-only (see LIVE_ONLY_TESTS in scripts/sync_core.py): core ships neither.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_current_weekday_cron_is_dated_by_the_fix_not_the_move():
    skill = (REPO_ROOT / ".claude/skills/midas-session-cadence/SKILL.md").read_text()
    line = next(l for l in skill.splitlines() if l.startswith("- **Weekday session**"))
    current = re.search(r"`0 22 \* \* 1-5` since (\d{4}-\d{2}-\d{2})", line)
    assert current, "the skill must date the current cron"
    meth = (REPO_ROOT / "METHODOLOGY.md").read_text()
    entry = meth[meth.find('<a id="missing-session-2026-10-02">') :].split("\n- <a id=")[0]
    assert "The cron is now `0 22 * * 1-5`" in entry
    assert entry.split("**")[1].startswith(current.group(1)), (
        "current cron is dated before the entry that fixed it"
    )


def _stale_deploy_dates() -> dict[str, str]:
    """The date each operational doc gives for the stale Worker schedule."""
    pats = {
        ".github/workflows/session-watchdog.yml": r"# secret\. From (\d{4}-\d{2}-\d{2}) the deployed",
        "workers/trigger-gate/README.md": r"\*\*Deploy from an up-to-date `main`\.\*\* From (\d{4}-\d{2}-\d{2}) the deployed",
        ".claude/skills/midas-session-cadence/SKILL.md": r"and from (\d{4}-\d{2}-\d{2}) it held the pre-move cron",
    }
    out = {}
    for rel, pat in pats.items():
        m = re.search(pat, (REPO_ROOT / rel).read_text())
        assert m, f"{rel} no longer dates the stale Worker deploy"
        out[rel] = m.group(1)
    wf = (REPO_ROOT / ".github/workflows/session-watchdog.yml").read_text()
    m = re.search(r"on (\d{4}-\d{2}-\d{2})\.\.\d{2}-\d{2} the deployed copy", wf)
    assert m, "the watchdog issue body no longer dates the stale deploy"
    out["issue body"] = m.group(1)
    return out


def test_stale_worker_deploy_is_dated_by_the_recorded_modification():
    """Regression: four docs dated the stale deploy 2026-09-28, a day before the
    Worker schedule's recorded last-modified time (METHODOLOGY says 2026-09-29)."""
    meth = (REPO_ROOT / "METHODOLOGY.md").read_text()
    m = re.search(r"last modified (\d{4}-\d{2}-\d{2}) \d{2}:\d{2}", meth)
    assert m, "METHODOLOGY must record when the deployed schedule was modified"
    for where, day in _stale_deploy_dates().items():
        assert day == m.group(1), f"{where} dates the stale deploy {day}, not {m.group(1)}"


def test_weekday_cron_history_has_no_gap_day():
    """Regression: `0 20` held 'before 2026-09-28' and `0-4` 'from 2026-09-29', so
    09-28 (whose session ran at 20:00) was covered by neither."""
    from datetime import date, timedelta

    skill = (REPO_ROOT / ".claude/skills/midas-session-cadence/SKILL.md").read_text()
    line = next(l for l in skill.splitlines() if l.startswith("- **Weekday session**"))
    old = re.search(r"`0 20 \* \* 1-5` through (\d{4}-\d{2}-\d{2})", line)
    new = re.search(r"`0 22 \* \* 0-4` from (\d{4}-\d{2}-\d{2})", line)
    assert old and new, "the skill must state both ends of the 0 20 -> 0-4 handover"
    assert date.fromisoformat(new.group(1)) - date.fromisoformat(old.group(1)) == timedelta(days=1)
