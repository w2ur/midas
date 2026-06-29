"""Drift guard: each live trading agent's resolved universe is pinned by a
compact (count, sha256-of-sorted-tickers) fingerprint.

This catches SILENT resolver drift — a committed `data/universes/*.json` refresh,
a roster universe change, or a resolver bug that shifts which tickers an agent
trades — without inlining hundreds of ticker symbols. Regenerate the expected
fingerprints deliberately (and review the diff) when a universe legitimately
changes:

    python - <<'PY'
    import hashlib
    from engine.config import get_config, resolve_agent_universe
    cfg = get_config()
    for aid in cfg.trading_roster:
        t = sorted(resolve_agent_universe(cfg.roster[aid]))
        print(aid, len(t), hashlib.sha256("\\n".join(t).encode()).hexdigest())
    PY
"""

from __future__ import annotations

import hashlib

from engine.config import get_config, reset_config_cache, resolve_agent_universe

# agent_id → (expected ticker count, sha256 of "\n".join(sorted(tickers)))
EXPECTED_UNIVERSE_FINGERPRINTS: dict[str, tuple[int, str]] = {
    "monsieur-forex": (
        10,
        "5c9187fea3014a1a23ae0ee45cf07e6f7b55b9ccc7d0890219b87128c7074c88",
    ),
    "steady-eddie-eur": (
        513,
        "190da585c43736c712a64ac74500df6917af1cbf93e03bf60138b4a32b752c79",
    ),
    "steady-eddie-usd": (
        503,
        "f25fa21ed02afd6fc30012b8f3ba1bac1f154d588e88e33f4c958e070ea139d5",
    ),
    "sharp-shooter-eur": (
        525,
        "40d1094a30c91d36a51734eef0bd7bcf1a7edd6fe047783e12befe3d60e9df81",
    ),
    "sharp-shooter-usd": (
        515,
        "aa36c52e9b1544904a202711ab9639534d0ca59ed8bb1d924f49582fe33a0647",
    ),
    "world": (
        1079,
        "dad966b4c91841f0f8b6256a523de58fc238510f704d077be512c8f1cb7be70a",
    ),
    "goldfinger": (
        7,
        "2e921c1b6c8a81636a9bac3846a555901fdd35e581f5105b266836eefce3f580",
    ),
    "yolo-sapiens-eur": (
        546,
        "b33a529ba833dc696d65cfcab1fc391bf02ce12cfa10913a0eefe0c1abf04246",
    ),
    "yolo-sapiens-usd": (
        553,
        "9b449864007982d7e0ed4a19f468b431cebcf9dac4afc170e5143cfb1267c4e9",
    ),
    "satoshi": (
        14,
        "bfd58ce4bd6c378edd59b13276c7d65288ffe308990eb826d683fbfdfb9c3380",
    ),
}


def _fingerprint(tickers: list[str]) -> tuple[int, str]:
    ordered = sorted(tickers)
    return len(ordered), hashlib.sha256("\n".join(ordered).encode()).hexdigest()


def test_trading_roster_matches_expected_set() -> None:
    """A new or removed live trading agent must be a deliberate change."""
    reset_config_cache()
    assert set(get_config().trading_roster) == set(EXPECTED_UNIVERSE_FINGERPRINTS)


def test_each_agent_resolved_universe_is_pinned() -> None:
    reset_config_cache()
    cfg = get_config()
    for agent_id in cfg.trading_roster:
        expected = EXPECTED_UNIVERSE_FINGERPRINTS[agent_id]
        actual = _fingerprint(resolve_agent_universe(cfg.roster[agent_id]))
        assert actual == expected, (
            f"{agent_id}: resolved universe drifted "
            f"(count/sha256 {actual} != expected {expected}). If a universe "
            "legitimately changed, regenerate the fingerprint and review the diff."
        )
