"""`scripts/derive_legacy_benchmark_marks.py` against a throwaway git history.

The script reads each legacy benchmark row's marks from the store as it stood
in the commit that wrote the row, and accepts them only if they reproduce the
published value exactly. These tests build that history in a temp repo: a
session writes a row that forward-fills Friday into Monday, the store later
gains Monday's close, and the sidecar must record what the WRITER saw
(Friday), not what the store holds now.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date

import pytest

import scripts.derive_legacy_benchmark_marks as derive
from engine.baselines import compute_passive_benchmark, marks_sidecar_path
from engine.config import BenchmarkSpec, get_config


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _commit(root, message):
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def _write_store(ticker, rows):
    ohlcv = get_config().ohlcv_dir
    ohlcv.mkdir(parents=True, exist_ok=True)
    (ohlcv / f"{ticker}.jsonl").write_text(
        "".join(json.dumps({"date": d, "close": c}) + "\n" for d, c in rows)
    )


def _legacy(rows):
    keep = ("date", "portfolio_value", "cash", "positions_value", "currency")
    return [{k: r[k] for k in keep} for r in rows]


def _priced_series(cfg):
    return derive.series_to_derive(cfg)


@pytest.fixture
def repo(midas_data_root, monkeypatch):
    root = midas_data_root
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "commit.gpgsign", "false")
    monkeypatch.setattr(derive, "REPO_ROOT", root)
    return root


def _publish_all(cfg, to_date):
    """Write legacy rows (no mark fields) for every priced series."""
    for path, ticker in _priced_series(cfg):
        spec_rows = compute_passive_benchmark(
            BenchmarkSpec("t", ticker, "EUR"), cfg.day_one, to_date
        )
        target = derive.REPO_ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(_legacy(spec_rows), indent=2) + "\n")


def _seed_all(cfg, rows):
    for _, ticker in _priced_series(cfg):
        _write_store(ticker, rows)


_FRI = [("2026-04-17", 100.0)]
_MON = _FRI + [("2026-04-20", 120.0)]


def test_marks_come_from_the_writers_store(repo, capsys):
    cfg = get_config()
    _seed_all(cfg, _FRI)
    _publish_all(cfg, date(2026, 4, 20))
    _commit(repo, "session 04-20: Monday forward-fills Friday")
    _seed_all(cfg, _MON)  # Monday's close lands afterwards
    _commit(repo, "store: Monday lands")

    assert derive.main([]) == derive.EXIT_OK

    path, _ = _priced_series(cfg)[0]
    sidecar = json.loads(marks_sidecar_path(repo / path).read_text())
    monday = {r["date"]: r for r in sidecar}["2026-04-20"]
    assert monday == {
        "date": "2026-04-20",
        "mark_date": "2026-04-17",
        "mark_close": 100.0,
        "base_date": "2026-04-17",
        "base_close": 100.0,
    }
    assert [r["date"] for r in sidecar] == sorted(r["date"] for r in sidecar)


def test_a_row_that_does_not_reproduce_is_a_finding(repo, capsys):
    """Exact reproduction is the acceptance test: a hand-edited value fails it."""
    cfg = get_config()
    _seed_all(cfg, [("2026-04-17", 100.0), ("2026-04-20", 120.0)])
    _publish_all(cfg, date(2026, 4, 20))
    path, _ = _priced_series(cfg)[0]
    rows = json.loads((repo / path).read_text())
    rows[-1]["portfolio_value"] += 0.01
    (repo / path).write_text(json.dumps(rows, indent=2) + "\n")
    _commit(repo, "session")

    assert derive.main([]) == derive.EXIT_FINDING
    out = capsys.readouterr().out
    assert "underived: 2026-04-20" in out
    sidecar = json.loads(marks_sidecar_path(repo / path).read_text())
    assert "2026-04-20" not in {r["date"] for r in sidecar}


def test_no_derivable_row_is_unknown_and_writes_nothing(repo, capsys):
    cfg = get_config()
    _seed_all(cfg, _FRI)
    _publish_all(cfg, date(2026, 4, 17))
    for _, ticker in _priced_series(cfg):
        (get_config().ohlcv_dir / f"{ticker}.jsonl").unlink(missing_ok=True)
    _commit(repo, "rows without a store")

    assert derive.main([]) == derive.EXIT_UNKNOWN
    assert not list((repo / "data" / "baselines").glob("*/*_marks.json"))


def test_an_unresolvable_ref_is_unknown(repo):
    _git(repo, "commit", "-q", "--allow-empty", "-m", "x")
    assert derive.main(["--ref", "no-such-ref"]) == derive.EXIT_UNKNOWN


def test_dry_run_writes_nothing(repo):
    cfg = get_config()
    _seed_all(cfg, _FRI)
    _publish_all(cfg, date(2026, 4, 18))
    _commit(repo, "session")
    assert derive.main(["--dry-run"]) == derive.EXIT_OK
    assert not list((repo / "data" / "baselines").glob("*/*_marks.json"))


def test_an_existing_entry_is_never_changed(repo, capsys):
    """The sidecar is frozen by the append-only gate like any dated row."""
    cfg = get_config()
    _seed_all(cfg, _FRI)
    _publish_all(cfg, date(2026, 4, 18))
    _commit(repo, "session")
    assert derive.main([]) == derive.EXIT_OK
    path, _ = _priced_series(cfg)[0]
    sidecar_path = marks_sidecar_path(repo / path)
    rows = json.loads(sidecar_path.read_text())
    rows[0]["mark_close"] = 999.0
    sidecar_path.write_text(json.dumps(rows, indent=2) + "\n")
    before = sidecar_path.read_bytes()

    assert derive.main([]) == derive.EXIT_FINDING
    assert sidecar_path.read_bytes() == before
    assert "disagrees" in capsys.readouterr().out


def test_cash_flat_benchmarks_get_no_sidecar(midas_data_root):
    cfg = get_config()
    cash = [
        a for a in cfg.trading_roster
        if cfg.roster[a].benchmark is not None
        and cfg.roster[a].benchmark.is_cash_flat
    ]
    paths = {p for p, _ in derive.series_to_derive(cfg)}
    for agent in cash:
        assert f"data/baselines/{agent}/benchmark.json" not in paths
