"""Passive benchmark rows record their marks; the merge classifies mismatches.

Background (plan subtask 1.5, revised 2026-10-04). `merge_baseline_series`
refused every published point a recomputation disagreed with as one
undifferentiated count: 1,830 refusals on the 2026-10-01 session, of which
11 were genuinely wrong prices. Most of the rest were benchmark points that
forward-filled a close which had not landed yet (the store filled it later,
so the recomputation moves forever) and coin-flip path recomputes. A count
nobody can act on is a guard nobody reads.

So a benchmark row now records which closes it was priced from
(`mark_date`/`mark_close` and `base_date`/`base_close`), and a mismatch is
classified against the store:

- ``concern``: the store's mark/base ratio differs from the recorded one —
  a published close was revised. The only class that reaches the session's
  ``Concerns:`` path.
- ``stale_mark``: the row forward-filled, and the store now holds a close
  after its mark. Expected; the published row is right for what it saw.
- ``rescaled``: the ratio holds but the closes changed (a units or split
  rebase of the whole history). A ratio series cancels a constant factor.
- ``unclassified``: a legacy row with no recorded marks anywhere.
- ``path_recompute``: a coin-flip mismatch (until plan 1.6 makes it stateful).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from engine.baselines import (
    MergeCounts,
    build_all_baselines,
    compute_passive_benchmark,
    merge_baseline_series,
)
from engine.config import BenchmarkSpec, get_config

_SPEC = BenchmarkSpec("Test", "TEST", "EUR")


def _write_store(ticker: str, rows: list[tuple[str, float]]) -> None:
    ohlcv = get_config().ohlcv_dir
    ohlcv.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"date": d, "close": c}) for d, c in rows]
    (ohlcv / f"{ticker}.jsonl").write_text("\n".join(lines) + "\n")


def _closes(rows: list[tuple[str, float]]) -> dict[str, float]:
    return {d: c for d, c in rows}


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n")


def _strip_marks(rows: list[dict]) -> list[dict]:
    keep = ("date", "portfolio_value", "cash", "positions_value", "currency")
    return [{k: r[k] for k in keep} for r in rows]


def _sidecar(rows: list[dict]) -> list[dict]:
    keys = ("date", "mark_date", "mark_close", "base_date", "base_close")
    return [{k: r[k] for k in keys} for r in rows]


# Thu 04-16 is the base, Fri 04-17 trades, Mon 04-20 has not landed yet when
# the row is first published, so the Mon row forward-fills Friday's close.
_FIRST_STORE = [("2026-04-16", 100.0), ("2026-04-17", 110.0)]
_FROM, _TO = date(2026, 4, 16), date(2026, 4, 20)


def _publish(path: Path, store: list[tuple[str, float]], *, legacy: bool = False):
    _write_store("TEST", store)
    rows = compute_passive_benchmark(_SPEC, _FROM, _TO)
    if legacy:
        _write(path, _strip_marks(rows))
        _write(path.with_name("benchmark_marks.json"), _sidecar(rows))
    else:
        _write(path, rows)
    return rows


def _remerge(path: Path, store: list[tuple[str, float]], **kw) -> MergeCounts:
    _write_store("TEST", store)
    computed = compute_passive_benchmark(_SPEC, _FROM, _TO)
    return merge_baseline_series(path, computed, closes=_closes(store), **kw)


# ---------------------------------------------------------------------------
# compute_passive_benchmark records its marks
# ---------------------------------------------------------------------------


def test_rows_record_the_closes_they_were_priced_from(midas_data_root):
    _write_store("TEST", _FIRST_STORE)
    rows = {r["date"]: r for r in compute_passive_benchmark(_SPEC, _FROM, _TO)}

    assert rows["2026-04-16"]["mark_date"] == "2026-04-16"
    assert rows["2026-04-16"]["base_date"] == "2026-04-16"
    # Saturday..Monday forward-fill Friday: the mark says so.
    for d in ("2026-04-18", "2026-04-19", "2026-04-20"):
        assert rows[d]["mark_date"] == "2026-04-17"
        assert rows[d]["mark_close"] == 110.0
        assert rows[d]["base_date"] == "2026-04-16"
        assert rows[d]["base_close"] == 100.0
    # The value is exactly what the marks say, by construction.
    r = rows["2026-04-20"]
    assert r["portfolio_value"] == get_config().initial_capital * (
        r["mark_close"] / r["base_close"]
    )


def test_cash_flat_rows_carry_no_marks(midas_data_root):
    """EUR_CASH_FLAT reads no price: there is no close to record."""
    spec = BenchmarkSpec("Cash", "EUR_CASH_FLAT", "EUR")
    rows = compute_passive_benchmark(spec, _FROM, _TO)
    assert all("mark_date" not in r for r in rows)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_identical_replay_counts_nothing(midas_data_root, tmp_path, capsys):
    path = tmp_path / "benchmark.json"
    _publish(path, _FIRST_STORE)
    assert _remerge(path, _FIRST_STORE) == MergeCounts()
    assert "[WARN]" not in capsys.readouterr().out


def test_a_late_close_is_a_stale_mark_not_a_concern(midas_data_root, tmp_path, capsys):
    path = tmp_path / "benchmark.json"
    published = _publish(path, _FIRST_STORE)

    counts = _remerge(path, _FIRST_STORE + [("2026-04-20", 120.0)])

    # Only the Monday row forward-filled across a close that later landed.
    assert counts == MergeCounts(stale_mark=1)
    assert counts.concern == 0
    assert "[WARN]" not in capsys.readouterr().out
    assert json.loads(path.read_text()) == published, "published rows must not move"


def test_a_revised_close_on_a_recorded_mark_is_a_concern(midas_data_root, tmp_path, capsys):
    """The fail-once check: a planted revision of a recorded mark fires."""
    path = tmp_path / "benchmark.json"
    published = _publish(path, _FIRST_STORE)

    counts = _remerge(path, [("2026-04-16", 100.0), ("2026-04-17", 111.0)])

    # 04-17 and the three days forward-filling it; 04-16 is its own base.
    assert counts == MergeCounts(concern=4)
    out = capsys.readouterr().out
    assert out.count("[WARN]") == 4
    assert "2026-04-17" in out and "110.0" in out and "111.0" in out
    assert json.loads(path.read_text()) == published


def test_a_revised_base_is_a_concern(midas_data_root, tmp_path):
    path = tmp_path / "benchmark.json"
    _publish(path, _FIRST_STORE)
    counts = _remerge(path, [("2026-04-16", 99.0), ("2026-04-17", 110.0)])
    # 04-16 itself is excluded (mark_date == base_date); the four later rows
    # all divide by the revised base.
    assert counts.concern == 4
    assert counts.rescaled == 1


def test_a_whole_history_rescale_is_not_a_concern(midas_data_root, tmp_path, capsys):
    """The other half of the fail-once check: x2 across the store cancels."""
    path = tmp_path / "benchmark.json"
    _publish(path, _FIRST_STORE)

    counts = _remerge(path, [(d, c * 2) for d, c in _FIRST_STORE])

    assert counts == MergeCounts(rescaled=5)
    assert "[WARN]" not in capsys.readouterr().out


def test_an_inexact_rescale_is_still_not_a_concern(midas_data_root, tmp_path):
    """x3 does not divide out exactly in binary floating point; 1e-12 relative does."""
    path = tmp_path / "benchmark.json"
    store = [("2026-04-16", 100.1), ("2026-04-17", 123.45)]
    _publish(path, store)
    scaled = [(d, c * 3) for d, c in store]
    assert (scaled[1][1] / scaled[0][1]) != (store[1][1] / store[0][1]), (
        "fixture must exercise the tolerance, not exact equality"
    )
    counts = _remerge(path, scaled)
    assert counts.concern == 0
    assert counts.rescaled == 5


def test_a_rescale_plus_a_late_close_is_a_stale_mark(midas_data_root, tmp_path):
    path = tmp_path / "benchmark.json"
    _publish(path, _FIRST_STORE)
    counts = _remerge(
        path, [(d, c * 2) for d, c in _FIRST_STORE] + [("2026-04-20", 240.0)]
    )
    assert counts == MergeCounts(stale_mark=1, rescaled=4)


def test_a_mark_the_store_no_longer_holds_is_a_concern(midas_data_root, tmp_path):
    """An unconfirmable mark is not a confirmed one: fail toward the concern."""
    path = tmp_path / "benchmark.json"
    _publish(path, _FIRST_STORE)
    counts = _remerge(path, [("2026-04-16", 100.0), ("2026-04-20", 110.0)])
    assert counts.concern == 4


def test_the_initial_row_is_never_a_concern(midas_data_root, tmp_path):
    """mark_date == base_date: the value is the initial capital by construction."""
    path = tmp_path / "benchmark.json"
    _write_store("TEST", [("2026-04-16", 100.0)])
    _write(path, compute_passive_benchmark(_SPEC, _FROM, _FROM))

    _write_store("TEST", [("2026-04-16", 50.0)])
    counts = merge_baseline_series(
        path,
        compute_passive_benchmark(_SPEC, _FROM, _FROM),
        closes={"2026-04-16": 50.0},
    )
    assert counts == MergeCounts(rescaled=1)


# ---------------------------------------------------------------------------
# Legacy rows: no fields on the row, marks in the sidecar
# ---------------------------------------------------------------------------


def test_adding_the_fields_refuses_nothing_on_a_legacy_row(midas_data_root, tmp_path):
    """A legacy row compares on value and currency only, and is never rewritten."""
    path = tmp_path / "benchmark.json"
    _write_store("TEST", _FIRST_STORE)
    legacy = _strip_marks(compute_passive_benchmark(_SPEC, _FROM, _TO))
    _write(path, legacy)
    before = path.read_bytes()

    counts = _remerge(path, _FIRST_STORE)

    assert counts == MergeCounts()
    assert path.read_bytes() == before


def test_a_legacy_row_without_a_sidecar_is_unclassified(midas_data_root, tmp_path, capsys):
    path = tmp_path / "benchmark.json"
    _write_store("TEST", _FIRST_STORE)
    _write(path, _strip_marks(compute_passive_benchmark(_SPEC, _FROM, _TO)))

    counts = _remerge(path, [("2026-04-16", 100.0), ("2026-04-17", 111.0)])

    assert counts == MergeCounts(unclassified=4)
    assert "[WARN]" not in capsys.readouterr().out


def test_the_sidecar_classifies_a_legacy_row(midas_data_root, tmp_path):
    path = tmp_path / "benchmark.json"
    _publish(path, _FIRST_STORE, legacy=True)
    assert _remerge(path, _FIRST_STORE + [("2026-04-20", 120.0)]) == MergeCounts(
        stale_mark=1
    )
    assert _remerge(path, [("2026-04-16", 100.0), ("2026-04-17", 111.0)]).concern == 4
    assert _remerge(path, [(d, c * 2) for d, c in _FIRST_STORE]) == MergeCounts()


def test_row_fields_outrank_the_sidecar(midas_data_root, tmp_path):
    path = tmp_path / "benchmark.json"
    rows = _publish(path, _FIRST_STORE)
    # A sidecar that disagrees with the row: the row's own fields win.
    wrong = [dict(r, mark_close=999.0) for r in _sidecar(rows)]
    _write(path.with_name("benchmark_marks.json"), wrong)
    assert _remerge(path, _FIRST_STORE + [("2026-04-20", 120.0)]) == MergeCounts(
        stale_mark=1
    )


def test_an_unreadable_sidecar_warns_and_classifies_nothing(
    midas_data_root, tmp_path, capsys
):
    path = tmp_path / "benchmark.json"
    _publish(path, _FIRST_STORE, legacy=True)
    path.with_name("benchmark_marks.json").write_text("{not json")
    counts = _remerge(path, [("2026-04-16", 100.0), ("2026-04-17", 111.0)])
    assert counts == MergeCounts(unclassified=4)
    assert "[WARN]" in capsys.readouterr().out


def test_msci_world_sidecar_name(midas_data_root, tmp_path):
    path = tmp_path / "global" / "msci_world.json"
    _write_store("TEST", _FIRST_STORE)
    rows = compute_passive_benchmark(_SPEC, _FROM, _TO)
    _write(path, _strip_marks(rows))
    _write(path.with_name("msci_world_marks.json"), _sidecar(rows))
    assert _remerge(path, [("2026-04-16", 100.0), ("2026-04-17", 111.0)]).concern == 4


# ---------------------------------------------------------------------------
# Coin flip, appends, restatement
# ---------------------------------------------------------------------------


def test_a_coin_flip_mismatch_is_a_path_recompute(tmp_path, capsys):
    path = tmp_path / "coinflip.json"
    row = {"date": "2026-04-17", "portfolio_value": 1.0, "cash": 0.0,
           "positions_value": 1.0, "currency": "EUR"}
    _write(path, [row])
    counts = merge_baseline_series(
        path, [dict(row, portfolio_value=2.0)], kind="coinflip"
    )
    assert counts == MergeCounts(path_recompute=1)
    assert counts.concern == 0
    assert "[WARN]" not in capsys.readouterr().out


def test_appends_are_counted(midas_data_root, tmp_path):
    path = tmp_path / "benchmark.json"
    _write_store("TEST", _FIRST_STORE)
    assert merge_baseline_series(
        path, compute_passive_benchmark(_SPEC, _FROM, _TO), closes=_closes(_FIRST_STORE)
    ) == MergeCounts(appended=5)


def test_restate_overwrites_and_classifies_nothing(midas_data_root, tmp_path):
    path = tmp_path / "benchmark.json"
    _publish(path, _FIRST_STORE)
    revised = [("2026-04-16", 100.0), ("2026-04-17", 111.0)]
    assert _remerge(path, revised, restate=True) == MergeCounts()
    assert json.loads(path.read_text())[1]["mark_close"] == 111.0


def test_counts_add_up():
    a = MergeCounts(appended=1, stale_mark=2, concern=1)
    b = MergeCounts(rescaled=3, path_recompute=4, unclassified=5)
    total = a + b
    assert total == MergeCounts(1, 2, 3, 1, 5, 4)
    assert total.mismatched == 15


# ---------------------------------------------------------------------------
# The consumer: what step 9a prints
# ---------------------------------------------------------------------------

_DAYS = ["2026-04-17", "2026-04-18", "2026-04-19", "2026-04-20", "2026-04-21"]


def _seed_desk(cfg, closes: dict[str, list[float]]) -> dict[str, list[str]]:
    ramp = [100.0, 101.0, 102.0, 103.0, 104.0]
    tickers = {
        cfg.roster[a].benchmark.ticker
        for a in cfg.trading_roster
        if cfg.roster[a].benchmark is not None
    } | {cfg.global_reference.ticker}
    tickers.discard("EUR_CASH_FLAT")
    for t in tickers:
        _write_store(t, list(zip(_DAYS, closes.get(t, ramp))))
    _write_store("FAKE-A", list(zip(_DAYS, [10.0, 10.5, 11.0, 11.5, 12.0])))
    _write_store("FAKE-B", list(zip(_DAYS, [20.0, 20.5, 20.0, 19.5, 19.0])))
    return {a: ["FAKE-A", "FAKE-B"] for a in cfg.trading_roster}


def _build(universes):
    return build_all_baselines(
        universes_by_agent=universes,
        from_date=date(2026, 4, 17),
        to_date=date(2026, 4, 21),
    )


def test_only_concerns_reach_the_warn_path(midas_data_root, capsys):
    cfg = get_config()
    universes = _seed_desk(cfg, {})
    _build(universes)
    capsys.readouterr()

    ref = cfg.global_reference.ticker
    # Revise one recorded mark of the global reference only.
    _seed_desk(cfg, {ref: [100.0, 101.0, 102.0, 999.0, 104.0]})
    totals = _build(universes)

    out = capsys.readouterr().out
    assert totals.concern >= 1
    assert f"[WARN] baselines: {totals.concern} concern(s)" in out
    warn_lines = [ln for ln in out.splitlines() if "[WARN]" in ln]
    assert all("concern" in ln or "revised" in ln for ln in warn_lines)


def test_expected_classes_print_one_info_line_each(midas_data_root, capsys):
    cfg = get_config()
    universes = _seed_desk(cfg, {})
    _build(universes)
    capsys.readouterr()

    # x2 across every benchmark history: rescaled, never a concern.
    doubled = [200.0, 202.0, 204.0, 206.0, 208.0]
    tickers = {
        cfg.roster[a].benchmark.ticker
        for a in cfg.trading_roster
        if cfg.roster[a].benchmark is not None
    } | {cfg.global_reference.ticker}
    _seed_desk(cfg, {t: doubled for t in tickers})
    totals = _build(universes)

    out = capsys.readouterr().out
    assert totals.concern == 0
    assert totals.rescaled > 0
    assert "[WARN]" not in out
    info = [ln for ln in out.splitlines() if "[INFO] baselines:" in ln]
    assert len(info) == 1
    assert "rescaled" in info[0] and "not a concern" in info[0]
