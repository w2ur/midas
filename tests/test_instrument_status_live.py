"""The committed instrument status registry agrees with the committed evidence.

Live-only (`scripts.sync_core.LIVE_ONLY_TESTS`): it reads this repo's
`data/market/` quarantine, corporate-action ledger and price store, none of
which midas-core ships. The mechanism is tested on fixtures in
`tests/test_instrument_status.py`.

The invariant: the set of symbols the registry holds `suspended` is exactly the
set with an unadjudicated quarantine row. Every automated writer keeps it (a
tripwire refusal writes a quarantine row AND a suspension; a landed calendar
adjudication writes a ledger row AND clears), so a disagreement means a
hand-made change landed one half without the other. Both directions matter:

- a suspension the ledger already explains traps a tradable symbol (the case
  when plan 0.1's CTVA ledger row and this registry merge in either order);
- an unadjudicated refusal with no suspension is a frozen symbol the broker
  rail (Stage 1.3) would see as healthy, which is also what a deleted or
  emptied registry looks like.
"""

from __future__ import annotations

from engine import instrument_status as status
from engine.config import _LEGACY_ROOT

MARKET = _LEGACY_ROOT / "data" / "market"


def test_the_registry_is_committed_and_readable() -> None:
    path = MARKET / "instrument_status.json"
    assert path.exists(), (
        f"{path} is missing; rebuild it with `python -m engine.instrument_status seed`"
    )
    status.load(path)  # raises RegistryUnreadable


def test_suspended_set_matches_the_unadjudicated_quarantine() -> None:
    registry = status.load(MARKET / "instrument_status.json")
    suspended = {s for s, e in registry.items() if e.status == status.SUSPENDED}
    unadjudicated = set(
        status.unadjudicated_rows(
            MARKET / "quarantine", MARKET / "corporate_actions.jsonl", MARKET / "ohlcv"
        )
    )
    assert suspended - unadjudicated == set(), (
        "suspended although every quarantine row is adjudicated (ledger row or "
        "re-merge); clear with `python -m engine.instrument_status clear SYM --reason ...`"
    )
    assert unadjudicated - suspended == set(), (
        "unadjudicated quarantine rows with no suspension; adjudicate them or "
        "record the suspension"
    )
