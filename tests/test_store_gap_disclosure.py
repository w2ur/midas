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


# --- the exposure claims (follow-up review r7, r1 M-2) ---------------------
#
# The entry first said "No book held either listing", true only on the day it
# was written: goldfinger held 30 SGLN.MI through the weeks the listing's feed
# thinned to one row a week, and sold on a stale close. These derive every
# exposure claim from the books' own trade ledgers and the store.

PORTFOLIOS = REPO_ROOT / "data" / "portfolios"
OHLCV = REPO_ROOT / "data" / "market" / "ohlcv"


def _trades(ticker: str) -> dict[str, list[dict]]:
    import json

    out: dict[str, list[dict]] = {}
    for path in sorted(PORTFOLIOS.glob("*/trades.json")):
        rows = [t for t in json.loads(path.read_text(encoding="utf-8")) if t.get("ticker") == ticker]
        if rows:
            out[path.parent.name] = sorted(rows, key=lambda t: t["timestamp"])
    return out


def _store_closes(ticker: str) -> dict[str, float]:
    import json

    rows = (json.loads(line) for line in (OHLCV / f"{ticker}.jsonl").read_text().splitlines() if line.strip())
    return {r["date"]: r["close"] for r in rows}


def test_no_book_ever_traded_3eus_l() -> None:
    assert "No book ever traded `3EUS.L`" in _entry()
    assert _trades("3EUS.L") == {}
    assert _trades("SGLN.MI"), "the control: the other old listing WAS traded"


def test_the_sgln_mi_exposure_is_what_the_ledger_and_store_show() -> None:
    from datetime import date

    claim = re.search(
        r"`(?P<book>[a-z-]+)` did hold `SGLN\.MI`.*?held (?P<shares>\d+) shares from (?P<since>\d{4}-\d{2}-\d{2})"
        r".*?after (?P<thinned>\d{4}-\d{2}-\d{2}).*?sold all (?P<sold>\d+) on (?P<exit>\d{4}-\d{2}-\d{2}) "
        r"at (?P<price>[\d.]+), €(?P<value>[\d,.]+), which is the stored close of (?P<mark>\d{4}-\d{2}-\d{2})",
        _entry(),
        re.S,
    )
    assert claim, "the entry must state the SGLN.MI exposure"
    books = _trades("SGLN.MI")
    assert set(books) == {claim["book"]}, "every book that traded SGLN.MI must be named"

    position, since = 0.0, None
    for trade in books[claim["book"]]:
        position += trade["shares"] if trade["action"] == "BUY" else -trade["shares"]
        day = trade["timestamp"][:10]
        if trade["action"] == "SELL" and position == 0:
            exit_trade, exit_day = trade, day
        elif position == float(claim["shares"]):
            since = day
    assert since == claim["since"]
    assert exit_day == claim["exit"]
    assert exit_trade["shares"] == float(claim["sold"])
    assert f"{exit_trade['price']:.2f}" == claim["price"]
    assert f"{exit_trade['shares'] * exit_trade['price']:,.2f}" == claim["value"]

    closes = _store_closes("SGLN.MI")
    marks = sorted(d for d, c in closes.items() if c == exit_trade["price"] and d <= exit_day)
    assert marks[-1] == claim["mark"]
    # "thinned after D": D is the last date whose next stored row is the next weekday.
    days = sorted(closes)
    daily = [
        a for a, b in zip(days, days[1:])
        if (date.fromisoformat(b) - date.fromisoformat(a)).days == (3 if date.fromisoformat(a).weekday() == 4 else 1)
    ]
    assert max(daily) == claim["thinned"]
    assert claim["since"] < claim["thinned"] < claim["exit"], "held through the thinning"


#: The commit that imported 3EUS.MI with its full vendor history, before the
#: pre-consolidation rows were dropped.
IMPORT_COMMIT = "c0abd31f1"


def _closes_text(text: str) -> dict[str, float]:
    import json

    return dict(sorted((json.loads(l)["date"], json.loads(l)["close"]) for l in text.splitlines() if l.strip()))


@pytest.mark.skipif(_is_shallow(), reason="shallow clone has no history to derive from")
def test_the_3eus_consolidation_claims_are_what_the_store_shows() -> None:
    claim = re.search(
        r"stored from (?P<first>\d{4}-\d{2}-\d{2}) only.*?by a factor of (?P<l>\d+) on `3EUS\.L` "
        r"\((?P<l0>[\d-]+) to (?P<l1>[\d-]+)\) and (?P<m>\d+) on `3EUS\.MI` \((?P<m0>[\d-]+) to (?P<m1>[\d-]+)\)",
        _entry(),
        re.S,
    )
    assert claim, "the entry must state where 3EUS.MI starts and why"
    stored = _closes_text((OHLCV / "3EUS.MI.jsonl").read_text())
    assert min(stored) == claim["first"] == claim["m1"]
    imported = _closes_text(
        subprocess.run(
            ["git", "show", f"{IMPORT_COMMIT}:data/market/ohlcv/3EUS.MI.jsonl"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout
    )
    retired = _closes_text((OHLCV / "3EUS.L.jsonl").read_text())
    assert round(imported[claim["m1"]] / imported[claim["m0"]]) == int(claim["m"])
    assert round(retired[claim["l1"]] / retired[claim["l0"]]) == int(claim["l"])
    # Every stored bar is on one basis: no step anywhere near the factor.
    days = sorted(stored)
    assert max(stored[b] / stored[a] for a, b in zip(days, days[1:])) < 2
