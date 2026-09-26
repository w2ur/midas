"""The store-gap disclosure states counts the store's own history derives.

METHODOLOGY `#store-gaps-2026-09-26` says how many daily closes the backfill
recovered, per date, and in how many files, and that no stored price changed.
Those are claims about one commit (`BACKFILL_COMMIT`), so this test derives
them from that commit's diff and holds the prose to it. The derivation reads
history, so it runs on a full clone only, like `TestRegressionCitations`.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ANCHOR = "store-gaps-2026-09-26"
#: The `[data]` commit that inserted the recovered rows.
BACKFILL_COMMIT = "c399a9d16"


def _entry() -> str:
    text = (REPO_ROOT / "METHODOLOGY.md").read_text(encoding="utf-8")
    start = text.find(f'<a id="{ANCHOR}"></a>')
    assert start != -1, f"METHODOLOGY has no #{ANCHOR} entry"
    end = text.find("\n- <a id=", start)
    return text[start:end]


def _stated() -> tuple[dict[str, int], int, int]:
    listing = re.search(
        r"The rows recovered, by date: (.*?), which makes ([\d,]+) rows in ([\d,]+) files",
        _entry(),
        re.S,
    )
    assert listing, "the entry must list the recovered rows by date"
    per_date = {
        d: int(n) for d, n in re.findall(r"`(\d{4}-\d{2}-\d{2})`: (\d+)", listing.group(1))
    }
    singles = re.search(r"one each on (.*)$", listing.group(1), re.S)
    for d in re.findall(r"`(\d{4}-\d{2}-\d{2})`", singles.group(1) if singles else ""):
        per_date[d] = 1
    return per_date, int(listing.group(2).replace(",", "")), int(listing.group(3).replace(",", ""))


def _is_shallow() -> bool:
    return (REPO_ROOT / ".git" / "shallow").exists()


def test_the_listing_parses_and_adds_up() -> None:
    per_date, total, files = _stated()
    assert len(per_date) >= 4, "the listing parsed to almost nothing — this checks nothing"
    assert sum(per_date.values()) == total
    assert 0 < files <= total


@pytest.mark.skipif(_is_shallow(), reason="shallow clone has no history to derive from")
def test_the_stated_counts_are_what_the_backfill_commit_inserted() -> None:
    diff = subprocess.run(
        ["git", "show", "--format=", "-U0", BACKFILL_COMMIT, "--", "data/market/ohlcv"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    per_date: Counter[str] = Counter()
    files: set[str] = set()
    deleted = 0
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            per_date[re.search(r'"date": "([\d-]+)"', line).group(1)] += 1
            files.add(current)
        elif line.startswith("-") and not line.startswith("---"):
            deleted += 1
    assert per_date, "the derivation found no inserted row — it checks nothing"
    assert deleted == 0, "the entry says no stored price changed"
    stated, total, n_files = _stated()
    assert stated == dict(per_date)
    assert total == sum(per_date.values())
    assert n_files == len(files)
