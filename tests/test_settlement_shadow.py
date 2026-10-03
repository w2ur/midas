"""Stage 2.2: `fetch_ohlcv --settlement-shadow` and its comparison script.

The shadow is READ-ONLY by contract: it must report what `merge_rows` would
insert, revise or refuse, and leave the store byte-identical. Every test drives
synthetic frames; no network.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

import scripts.fetch_ohlcv as fo
from scripts.compare_settlement_shadow import compare, parse_diff

_FIELDS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


def _frame(rows: dict[str, float]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in rows], name="Date")
    return pd.DataFrame(
        [[c, c, c, c, c, 1000] for c in rows.values()], index=idx, columns=pd.Index(_FIELDS)
    )


def _store(path, rows: dict[str, float]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for d, c in rows.items():
            f.write(
                json.dumps(
                    {"date": d, "open": c, "high": c, "low": c, "close": c, "adj_close": c, "volume": 1000}
                )
                + "\n"
            )


def test_window_start_counts_weekdays_not_calendar_days():
    # Fri 2026-10-02 minus 10 weekdays = Fri 2026-09-18.
    assert fo.settlement_window_start(date(2026, 10, 2)) == date(2026, 9, 18)
    # From a Monday the weekend is skipped: 10 weekdays back from Mon 10-05.
    assert fo.settlement_window_start(date(2026, 10, 5)) == date(2026, 9, 21)


def test_shadow_reports_insert_revision_and_tripwire_without_writing(tmp_path):
    path = tmp_path / "AAA.jsonl"
    _store(path, {"2026-09-21": 10.0, "2026-09-23": 10.0, "2026-09-24": 10.0})
    before = path.read_bytes()
    df = _frame(
        {
            "2026-09-21": 10.0,  # unchanged
            "2026-09-22": 10.5,  # interior hole the window refills -> insert
            "2026-09-23": 10.2,  # vendor revised a stored bar -> revision
            "2026-09-24": 30.0,  # >20% revision -> refused by the tripwire
        }
    )
    out = fo.shadow_merge(path, df, "2026-09-18")
    assert out["inserts"] == ["2026-09-22"]
    assert [r["date"] for r in out["revisions"]] == ["2026-09-23"]
    assert out["revisions"][0]["old_close"] == 10.0
    assert out["revisions"][0]["new_close"] == 10.2
    assert [q["date"] for q in out["quarantined"]] == ["2026-09-24"]
    # Read-only: store bytes untouched, no tmp or quarantine sidecar left behind.
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["AAA.jsonl"]


def test_run_writes_one_json_and_skips_symbols_without_a_store(tmp_path, monkeypatch):
    ohlcv = tmp_path / "ohlcv"
    ohlcv.mkdir()
    _store(ohlcv / "AAA.jsonl", {"2026-09-21": 10.0})

    class Cfg:
        ohlcv_dir = ohlcv

    monkeypatch.setattr(fo, "get_config", lambda: Cfg)
    monkeypatch.setattr(
        fo,
        "_fetch_symbol",
        lambda s, a, b, **k: _frame({"2026-09-21": 10.0, "2026-09-22": 11.0}) if s == "AAA" else None,
    )
    out_dir = tmp_path / "shadow"
    rc = fo.run_settlement_shadow(["AAA", "NEW"], date(2026, 10, 2), out_dir)
    assert rc == 0
    [report_file] = list(out_dir.glob("*.json"))
    report = json.loads(report_file.read_text())
    assert report["inserts"] == {"AAA": ["2026-09-22"]}
    assert report["skipped_no_store"] == ["NEW"]
    assert report["window_start"] == "2026-09-18"
    assert report["served"] == 1


def test_a_shadow_that_served_nothing_is_unknown_not_healthy(tmp_path, monkeypatch):
    ohlcv = tmp_path / "ohlcv"
    ohlcv.mkdir()
    _store(ohlcv / "AAA.jsonl", {"2026-09-21": 10.0})

    class Cfg:
        ohlcv_dir = ohlcv

    monkeypatch.setattr(fo, "get_config", lambda: Cfg)
    monkeypatch.setattr(fo, "_fetch_symbol", lambda *a, **k: None)
    assert fo.run_settlement_shadow(["AAA"], date(2026, 10, 2), tmp_path / "s") == fo.EXIT_SHADOW_NO_DATA


def test_shadow_flag_refuses_to_combine_with_other_modes(monkeypatch):
    monkeypatch.setattr("sys.argv", ["fetch_ohlcv.py", "--settlement-shadow", "--resweep-held"])
    with pytest.raises(SystemExit):
        fo.main()


DIFF = """\
diff --git a/data/market/ohlcv/AAA.jsonl b/data/market/ohlcv/AAA.jsonl
--- a/data/market/ohlcv/AAA.jsonl
+++ b/data/market/ohlcv/AAA.jsonl
@@ -3 +3,2 @@
-{"date": "2026-09-23", "close": 10.0}
+{"date": "2026-09-23", "close": 10.2}
+{"date": "2026-09-25", "close": 11.0}
diff --git a/data/market/ohlcv/BBB.jsonl b/data/market/ohlcv/BBB.jsonl
--- a/data/market/ohlcv/BBB.jsonl
+++ b/data/market/ohlcv/BBB.jsonl
@@ -9,0 +10 @@
+{"date": "2026-09-01", "close": 5.0}
"""


def test_parse_diff_separates_inserts_from_revisions():
    inserted, revised = parse_diff(DIFF)
    assert inserted == {("AAA", "2026-09-25"), ("BBB", "2026-09-01")}
    assert revised == {("AAA", "2026-09-23")}


def test_compare_flags_a_row_the_window_missed_and_ignores_older_ones():
    shadow = {
        "window_start": "2026-09-18",
        "inserts": {"AAA": ["2026-09-25"], "CCC": ["2026-09-24"]},
        "quarantined": [{"symbol": "CCC"}],
        "revisions": {"AAA": [{"date": "2026-09-23"}]},
    }
    real = {("AAA", "2026-09-25"), ("BBB", "2026-09-01"), ("DDD", "2026-09-22")}
    r = compare(real, shadow)
    # BBB 09-01 predates the window: not a miss. DDD 09-22 is in-window and absent.
    assert r["missing_from_shadow"] == [("DDD", "2026-09-22")]
    assert r["outside_window"] == [("BBB", "2026-09-01")]
    assert r["extra_in_shadow"] == [("CCC", "2026-09-24")]
    assert r["tripwire_hits"] == 1
    assert r["shadow_revisions"] == 1


def test_compare_control_identical_sets_report_no_miss():
    shadow = {"window_start": "2026-09-18", "inserts": {"AAA": ["2026-09-25"]}}
    assert compare({("AAA", "2026-09-25")}, shadow)["missing_from_shadow"] == []


# --- review fixes: what `compare` may call a miss ---------------------------


def test_regression_a_symbol_first_ingested_that_night_is_not_a_miss():
    # Regression: the shadow skips a symbol with no store file on purpose, but
    # compare() blamed it for the real run's first-ingest rows -> false exit 1.
    shadow = {
        "window_start": "2026-09-18",
        "end": "2026-10-02",
        "inserts": {},
        "skipped_no_store": ["NEWCO.L"],
    }
    real = {("NEWCO.L", "2026-09-29"), ("NEWCO.L", "2026-10-02")}
    r = compare(real, shadow)
    assert r["missing_from_shadow"] == []
    assert r["skipped_first_ingest"] == sorted(real)
    # Control: the same rows for a symbol the shadow DID cover are a miss.
    shadow["skipped_no_store"] = []
    assert compare(real, shadow)["missing_from_shadow"] == sorted(real)


def test_regression_rows_after_the_shadows_end_are_not_a_miss():
    # Regression: a --head taken after the evening close runs adds today-dated
    # rows the shadow (end = yesterday) could never insert.
    shadow = {"window_start": "2026-09-18", "end": "2026-10-02", "inserts": {}}
    r = compare({("AAA", "2026-10-03")}, shadow)
    assert r["missing_from_shadow"] == []
    assert compare({("AAA", "2026-10-02")}, shadow)["missing_from_shadow"] == [("AAA", "2026-10-02")]


def test_tripwire_added_is_the_shadows_hits_beyond_the_real_runs():
    # Regression: the plan asks what the wider window ADDS; only the absolute
    # shadow count was reported.
    shadow = {
        "window_start": "2026-09-18",
        "inserts": {},
        "quarantined": [
            {"symbol": "AAA", "date": "2026-09-24"},
            {"symbol": "BBB", "date": "2026-09-25"},
            {"symbol": "CCC", "date": "2026-09-26"},
        ],
    }
    real_q = {("AAA", "2026-09-24"), ("BBB", "2026-09-25")}
    r = compare(set(), shadow, real_q)
    assert (r["tripwire_hits"], r["real_quarantined"], r["tripwire_added"]) == (3, 2, 1)
    assert compare(set(), shadow, set())["tripwire_added"] == 3


def test_parse_quarantined_reads_the_real_runs_quarantine_appends():
    from scripts.compare_settlement_shadow import parse_quarantined

    diff = (
        "diff --git a/data/market/quarantine/APH.jsonl b/data/market/quarantine/APH.jsonl\n"
        "--- a/data/market/quarantine/APH.jsonl\n"
        "+++ b/data/market/quarantine/APH.jsonl\n"
        "@@ -2,0 +3 @@\n"
        '+{"symbol": "APH", "date": "2026-09-02", "kind": "new-row"}\n'
    )
    assert parse_quarantined(diff) == {("APH", "2026-09-02")}
    # Quarantine files are not mistaken for store inserts.
    assert parse_diff(diff) == (set(), set())


def test_shadow_report_records_the_base_sha_and_run(tmp_path, monkeypatch):
    ohlcv = tmp_path / "ohlcv"
    ohlcv.mkdir()
    _store(ohlcv / "AAA.jsonl", {"2026-09-21": 10.0})

    class Cfg:
        ohlcv_dir = ohlcv

    monkeypatch.setattr(fo, "get_config", lambda: Cfg)
    monkeypatch.setattr(fo, "_fetch_symbol", lambda *a, **k: _frame({"2026-09-21": 10.0}))
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    fo.run_settlement_shadow(["AAA"], date(2026, 10, 2), tmp_path / "s")
    [f] = (tmp_path / "s").glob("*.json")
    rep = json.loads(f.read_text())
    assert (rep["base_sha"], rep["run_id"], rep["event"]) == ("abc123", "42", "schedule")


def test_an_unknown_night_is_refused_by_the_comparison_cli(tmp_path, monkeypatch):
    # Regression: an exit-2 night still uploads a served=0 artifact; counting
    # it as a sample would pad the ten-night evidence with unknowns.
    import scripts.compare_settlement_shadow as cs

    rep = tmp_path / "r.json"
    rep.write_text(json.dumps({"window_start": "2026-09-18", "inserts": {}, "served": 0}))
    monkeypatch.setattr("sys.argv", ["c", str(rep), "--base", "a", "--head", "b"])
    monkeypatch.setattr(cs, "_git_diff", lambda b, h: DIFF)
    assert cs.main() == 2
    # Control: with symbols served the same inputs are a clean night.
    rep.write_text(json.dumps({"window_start": "2026-09-18", "inserts": {"AAA": ["2026-09-25"]}, "served": 5}))
    monkeypatch.setattr(cs, "_held", lambda: set())
    assert cs.main() == 0  # BBB 09-01 predates the window; AAA 09-25 is in the shadow
    # Control: a miss still exits 1.
    rep.write_text(json.dumps({"window_start": "2026-09-18", "inserts": {}, "served": 5}))
    assert cs.main() == 1


def test_regression_an_empty_git_range_is_unknown_not_a_clean_night(tmp_path, monkeypatch):
    # Regression: a wrong --head gave an empty diff and a green all-zero report,
    # and the test that should have caught it asserted rc 0 for that input.
    import scripts.compare_settlement_shadow as cs

    rep = tmp_path / "r.json"
    rep.write_text(json.dumps({"window_start": "2026-09-18", "inserts": {}, "served": 5}))
    monkeypatch.setattr("sys.argv", ["c", str(rep), "--base", "a", "--head", "a"])
    monkeypatch.setattr(cs, "_git_diff", lambda b, h: "")
    assert cs.main() == 2


def test_regression_a_row_the_real_run_adjudicated_is_not_a_miss():
    # Regression: `_adjudicate` re-merges an explained split row with the
    # tripwire off, so it is inserted AND quarantined in the real run; the
    # shadow refuses it. That is handled on both sides, not a stop condition.
    row = ("MNST", "2026-10-01")
    shadow = {"window_start": "2026-09-18", "inserts": {}, "quarantined": [{"symbol": "MNST", "date": "2026-10-01"}]}
    r = compare({row}, shadow, {row})
    assert r["missing_from_shadow"] == []
    assert r["adjudicated_by_real_run"] == [row]
    # Control: the same row with no quarantine on the real side IS a miss.
    r = compare({row}, shadow, set())
    assert r["missing_from_shadow"] == [row]
    # Control: quarantined in the real run only (shadow inserted nothing, did not refuse) is a miss.
    r = compare({row}, {"window_start": "2026-09-18", "inserts": {}}, {row})
    assert r["missing_from_shadow"] == [row]


def test_missing_rows_for_held_symbols_are_marked():
    shadow = {"window_start": "2026-09-18", "inserts": {}}
    real = {("HELD", "2026-09-22"), ("SCREEN", "2026-09-22")}
    r = compare(real, shadow, held={"HELD"})
    assert [p[0] for p in r["missing_from_shadow"]] == ["HELD", "SCREEN"]
    assert r["missing_held"] == [("HELD", "2026-09-22")]
    assert compare(real, shadow)["missing_held"] == []


def test_regression_a_failed_shadow_fetch_is_not_a_miss():
    # Regression: a transient vendor failure for one symbol put its real rows in
    # `missing_from_shadow` and exited 1, reading as a money-tier gap the window
    # never had the chance to recover.
    row = ("X", "2026-09-30")
    shadow = {"window_start": "2026-09-18", "end": "2026-10-02", "inserts": {}, "failed": ["X"], "served": 1}
    r = compare({row}, shadow, held={"X"})
    assert r["missing_from_shadow"] == [] and r["missing_held"] == []
    assert r["shadow_fetch_failed"] == [row]
    # Control: the same row with the fetch not failed IS a miss.
    r = compare({row}, {**shadow, "failed": []}, held={"X"})
    assert r["missing_from_shadow"] == [row]
    assert r["shadow_fetch_failed"] == []


def test_regression_the_script_run_by_path_can_read_holdings(tmp_path):
    # Regression: `python scripts/compare_settlement_shadow.py` puts scripts/ on
    # sys.path, `from scripts.fetch_ohlcv import` failed, and the broad except
    # left `missing_held` empty on every real invocation. Run it the documented
    # way, in a subprocess, from a directory that is not the repo root.
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "compare_settlement_shadow.py"
    code = (
        "import runpy, sys\n"
        "sys.path[:] = [p for p in sys.path if p not in ('', {root!r})]\n"
        "sys.path.insert(0, {scripts!r})  # what running the file by path gives\n"
        f"ns = runpy.run_path({str(script)!r}, run_name='cli')\n"
        "ns['_held']()\n"
    ).format(root=str(script.parents[1]), scripts=str(script.parent))
    p = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, env={"PATH": "/usr/bin"}
    )
    assert p.returncode == 0, p.stderr
    assert "holdings unreadable" not in p.stderr, p.stderr
