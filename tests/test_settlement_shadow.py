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
