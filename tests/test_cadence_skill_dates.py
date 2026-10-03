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
