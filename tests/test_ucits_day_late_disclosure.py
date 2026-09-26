"""The day-late UCITS disclosure names a set the store derives, not a typed list.

METHODOLOGY `#ucits-day-late-2026-09-25` states that a set of thin UCITS ETFs
is priced a day behind the rest of the universe. The funds it names are a
claim about the data, so this test derives the set from the price store as it
stood when the entry was written (`STORE_COMMIT`, the 2026-09-25 OHLCV
update) and holds the prose to it: the list, and the count written beside it.

The candidates are the two UCITS asset universes (`engine.universes.assets`),
which is where every one of these funds comes from; a symbol exactly one
trading day behind the rest of the store at that commit is "day late". A
symbol further behind (stuck for days) is a different fault and is not in the
set. The derivation reads history, so it runs on a full clone only, like
`TestRegressionCitations`.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from engine.universes.assets import (
    get_bearish_etf_ucits_tickers,
    get_commodities_eur_tickers,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ANCHOR = "ucits-day-late-2026-09-25"
#: The fetch-ohlcv commit the entry's set was derived from.
STORE_COMMIT = "c77d3029d"
#: Liquid names on the same exchanges, as the "rest of the store" reference.
REFERENCE = ("AAPL", "AIR.PA", "SAP.DE", "AZN.L")

_NUMBER_WORDS = {"seventeen": 17, "eighteen": 18, "nineteen": 19, "sixteen": 16}


def _entry() -> str:
    text = (REPO_ROOT / "METHODOLOGY.md").read_text(encoding="utf-8")
    start = text.find(f'<a id="{ANCHOR}"></a>')
    assert start != -1, f"METHODOLOGY has no #{ANCHOR} entry"
    end = text.find("\n- <a id=", start)
    return text[start:end]


def _named() -> set[str]:
    listing = re.search(r"The funds are (.*?)\.\s", _entry(), re.S)
    assert listing, "the entry must list the funds after 'The funds are'"
    return set(re.findall(r"`([A-Z0-9]+\.[A-Z]+)`", listing.group(1)))


def _dates_at(commit: str, symbol: str) -> list[str]:
    out = subprocess.run(
        ["git", "show", f"{commit}:data/market/ohlcv/{symbol}.jsonl"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return []
    return sorted(json.loads(l)["date"] for l in out.stdout.splitlines() if l.strip())


def _is_shallow() -> bool:
    return (REPO_ROOT / ".git" / "shallow").exists()


def test_the_count_in_the_prose_matches_the_list() -> None:
    named = _named()
    assert named, "the list parsed to nothing — this test would check nothing"
    words = re.search(r"\b(\w+) thinly traded UCITS", _entry())
    assert words and _NUMBER_WORDS.get(words.group(1).lower()) == len(named)


def test_every_named_fund_is_a_ucits_universe_member() -> None:
    ucits = set(get_bearish_etf_ucits_tickers()) | set(get_commodities_eur_tickers())
    assert _named() <= ucits


def _day_late(dates_of) -> set[str]:
    reference = [dates_of(s) for s in REFERENCE]
    assert all(reference), "a reference symbol is missing from the store"
    latest = max(d[-1] for d in reference)
    previous = max(d for d in reference[0] if d < latest)
    ucits = set(get_bearish_etf_ucits_tickers()) | set(get_commodities_eur_tickers())
    return {s for s in ucits if (dates := dates_of(s)) and dates[-1] == previous}


#: The data commit the named set is held to: the store as it stood when
#: 3EUS.MI was added to the entry. Pinned rather than read live (follow-up
#: review r8 M1, r9 M2): the nightly fetch rewrites the store, so after any
#: US-only holiday the reference symbols disagree about "the previous
#: trading day" and a live derivation goes red with nothing wrong in the prose.
DATA_COMMIT = "dc26dffdd"


@pytest.mark.skipif(_is_shallow(), reason="shallow clone has no history to derive from")
def test_the_named_set_is_what_the_store_showed_one_day_behind_at_the_data_commit() -> None:
    """Derived from the current universes and the store at `DATA_COMMIT`
    (follow-up review r8, r6 N-M2): pinned to the entry's first commit, the
    test could not see 3EUS.MI, which joined `bearish-etfs-ucits` a day later
    and is day-late too."""
    day_late = _day_late(lambda s: _dates_at(DATA_COMMIT, s))
    assert day_late, "the derivation found no day-late fund — it checks nothing"
    assert _named() == day_late


@pytest.mark.skipif(_is_shallow(), reason="shallow clone has no history to derive from")
def test_the_set_at_the_entrys_commit_is_the_named_set_less_later_members() -> None:
    at_commit = _day_late(lambda s: _dates_at(STORE_COMMIT, s))
    assert at_commit, "the derivation found no day-late fund — it checks nothing"
    assert at_commit <= _named()
    for later in _named() - at_commit:
        assert _dates_at(STORE_COMMIT, later) == [], f"{later} was in the store at the entry's commit"
