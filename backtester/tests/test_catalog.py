from datetime import date

from backtester.catalog import build_catalog


def _catalog():
    return build_catalog(date(2026, 7, 10))


def test_catalog_has_the_four_top_level_keys():
    cat = _catalog()
    assert set(cat) == {"presets", "universes", "date_bounds", "currencies"}


def test_date_bounds_and_currencies():
    cat = _catalog()
    assert cat["date_bounds"] == {"min": "2010-01-01", "max": "2026-07-10"}
    assert cat["currencies"] == ["EUR", "USD"]


def test_presets_non_empty_and_well_shaped():
    presets = _catalog()["presets"]
    assert presets
    for p in presets:
        assert set(p) == {
            "id",
            "label",
            "selector",
            "manager",
            "rules",
            "default_universe",
        }
        assert set(p["rules"]) == {"max_positions", "max_position_pct", "min_hold_days"}


def test_baselines_are_excluded():
    ids = {p["id"] for p in _catalog()["presets"]}
    assert ids.isdisjoint(
        {
            "baseline-60-40",
            "baseline-equal-weight",
            "baseline-voo-hold",
            "coin-flip-baseline",
        }
    )


def test_unimplemented_universe_preset_excluded():
    # dividend-aristocrats-drip points at the unimplemented "dividend-aristocrats" universe
    ids = {p["id"] for p in _catalog()["presets"]}
    assert "dividend-aristocrats-drip" not in ids


def test_presets_are_deduped_by_signal_payload():
    presets = _catalog()["presets"]
    keys = [
        (
            p["default_universe"],
            p["selector"],
            p["manager"],
            p["rules"]["max_positions"],
            p["rules"]["max_position_pct"],
            p["rules"]["min_hold_days"],
        )
        for p in presets
    ]
    assert len(keys) == len(
        set(keys)
    )  # no functional duplicates (golden-cross variants collapse)


def test_every_preset_universe_resolves():
    from engine.universes import resolve_universe

    for p in _catalog()["presets"]:
        assert resolve_universe(p["default_universe"])  # does not raise, non-empty


def test_every_offered_universe_resolves():
    from engine.universes import resolve_universe

    for u in _catalog()["universes"]:
        assert resolve_universe(u["id"])
        assert u["label"]
