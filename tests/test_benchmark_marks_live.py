"""The committed benchmark marks sidecars agree with the committed series.

Live-only (`scripts.sync_core.LIVE_ONLY_TESTS`): it reads this repo's
`data/baselines/`, which midas-core does not ship. The derivation is tested on
a throwaway history in `tests/test_derive_legacy_benchmark_marks.py`.

`scripts/derive_legacy_benchmark_marks.py` accepts a mark only when it
reproduces the published value exactly, so every committed entry must still
do so against the committed row: a sidecar that does not is either a
hand edit or a row that moved without its marks, and `merge_baseline_series`
would classify that row's mismatches against closes it was never priced from.
"""

from __future__ import annotations

import json
from pathlib import Path

import scripts.derive_legacy_benchmark_marks as derive
from engine.baselines import MARK_FIELDS, marks_sidecar_path
from engine.config import _LEGACY_ROOT, get_config

BASELINES = _LEGACY_ROOT / "data" / "baselines"


def inconsistencies(sidecar: Path, initial: float) -> list[str]:
    """Every way ``sidecar`` disagrees with the series beside it."""
    series_path = sidecar.with_name(sidecar.name.replace("_marks.json", ".json"))
    series = {r["date"]: r for r in json.loads(series_path.read_text())}
    entries = json.loads(sidecar.read_text())
    problems = []
    if [e["date"] for e in entries] != sorted({e["date"] for e in entries}):
        problems.append("entries are not sorted by unique date")
    for e in entries:
        if set(e) != {"date", *MARK_FIELDS}:
            problems.append(f"{e.get('date')}: fields {sorted(e)}")
            continue
        row = series.get(e["date"])
        if row is None:
            problems.append(f"{e['date']}: no published row")
        elif "mark_date" in row:
            problems.append(f"{e['date']}: the row records its own marks")
        elif not e["base_date"] <= e["mark_date"] <= e["date"]:
            problems.append(f"{e['date']}: marks out of order")
        elif initial * (e["mark_close"] / e["base_close"]) != row["portfolio_value"]:
            problems.append(f"{e['date']}: marks do not reproduce the published value")
    return problems


def _sidecars(root: Path = BASELINES) -> list[Path]:
    return sorted(root.glob("*/*_marks.json"))


def expected_sidecars() -> set[str]:
    """The sidecar of every priced benchmark series, under data/baselines.

    Derived from the same roster walk the derivation script runs
    (``series_to_derive``): every trading agent whose benchmark is not
    ``EUR_CASH_FLAT``, plus the global reference. Each of them carries
    legacy rows published before plan 1.5, so each must have a sidecar.
    """
    return {
        marks_sidecar_path(Path(rel)).relative_to("data/baselines").as_posix()
        for rel, _ticker in derive.series_to_derive(get_config())
    }


def sidecar_set_differences(root: Path) -> tuple[set[str], set[str]]:
    """``(missing, unexpected)`` sidecars under ``root`` against the roster."""
    found = {p.relative_to(root).as_posix() for p in _sidecars(root)}
    expected = expected_sidecars()
    return expected - found, found - expected


def test_every_priced_series_has_a_sidecar() -> None:
    """Exactly the priced series: a missing sidecar leaves that series'
    legacy rows ``unclassified``, an extra one is a sidecar nothing reads."""
    assert "global/msci_world_marks.json" in expected_sidecars()
    assert sidecar_set_differences(BASELINES) == (set(), set())


def test_a_hidden_sidecar_is_caught(tmp_path) -> None:
    """The control: the check above goes red on a COPY of the committed
    baselines with one agent's sidecar removed (the committed file is never
    touched), and on one with a sidecar for a series that is not priced.

    The guard it replaced asserted only that msci_world's sidecar existed and
    that there were at least two, so every agent's sidecar but one could go
    missing and it stayed green.
    """
    import shutil

    copy = tmp_path / "baselines"
    shutil.copytree(BASELINES, copy)
    hidden = sorted(n for n in expected_sidecars() if not n.startswith("global/"))[0]
    (copy / hidden).unlink()
    assert sidecar_set_differences(copy) == ({hidden}, set())

    (copy / hidden).write_text((BASELINES / hidden).read_text())
    (copy / "global" / "coinflip_marks.json").write_text("[]\n")
    assert sidecar_set_differences(copy) == (set(), {"global/coinflip_marks.json"})


def test_every_sidecar_reproduces_its_series() -> None:
    initial = get_config().initial_capital
    # Keyed on the path under data/baselines: every agent's sidecar is named
    # `benchmark_marks.json`, so keying on the bare name kept only the last one
    # and left every other agent's sidecar unchecked.
    found = {
        p.relative_to(BASELINES).as_posix(): inconsistencies(p, initial)
        for p in _sidecars()
    }
    assert found and not any(found.values()), found


def test_a_moved_value_is_caught(tmp_path) -> None:
    """The control: the check above goes red on one perturbed row."""
    source = BASELINES / "global"
    for name in ("msci_world.json", "msci_world_marks.json"):
        (tmp_path / name).write_text((source / name).read_text())
    rows = json.loads((tmp_path / "msci_world.json").read_text())
    rows[-1]["portfolio_value"] += 0.01
    (tmp_path / "msci_world.json").write_text(json.dumps(rows))
    assert inconsistencies(tmp_path / "msci_world_marks.json", 10_000.0)
