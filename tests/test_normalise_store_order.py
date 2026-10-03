"""Tests for scripts/normalise_store_order.py and scripts/verify_store_canonical.py."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import normalise_store_order as norm
from scripts import verify_store_canonical as verify


def _line(d: str, close: float = 1.0) -> str:
    return json.dumps({"date": d, "open": close, "high": close, "low": close,
                       "close": close, "adj_close": close, "volume": 1})


def _store(tmp: Path, files: dict[str, list[str]]) -> Path:
    d = tmp / "ohlcv"
    d.mkdir()
    for name, lines in files.items():
        (d / name).write_text("".join(x + "\n" for x in lines))
    return d


def test_normalise_sorts_without_reserialising_and_is_idempotent(tmp_path: Path) -> None:
    odd = '{"date":"2026-01-02",  "close": 2.0}'  # deliberately non-default spacing
    d = _store(tmp_path, {"A.jsonl": [odd, _line("2025-12-30"), _line("2026-01-05")],
                          "B.jsonl": [_line("2026-01-01")]})
    assert norm.main(["--dir", str(d)]) == 0  # dry run writes nothing
    assert (d / "A.jsonl").read_text().splitlines()[0] == odd
    assert norm.main(["--dir", str(d), "--apply"]) == 0
    lines = (d / "A.jsonl").read_text().splitlines()
    assert [json.loads(x)["date"] for x in lines] == ["2025-12-30", "2026-01-02", "2026-01-05"]
    assert odd in lines  # bytes preserved
    before = {p.name: p.read_bytes() for p in d.iterdir()}
    assert norm.main(["--dir", str(d), "--apply"]) == 0  # second run: nothing to do
    assert before == {p.name: p.read_bytes() for p in d.iterdir()}
    assert not list(d.glob("*.tmp"))


def test_normalise_refuses_conflicting_duplicates_and_broken_lines(tmp_path: Path, capsys) -> None:
    d = _store(tmp_path, {"D.jsonl": [_line("2026-01-02", 1.0), _line("2026-01-02", 2.0)],
                          "E.jsonl": [_line("2026-01-02"), "{broken"],
                          "F.jsonl": [_line("2026-01-02"), _line("2026-01-02")]})
    orig = {p.name: p.read_bytes() for p in d.iterdir()}
    assert norm.main(["--dir", str(d), "--apply"]) == 1
    err = capsys.readouterr().err
    assert "D.jsonl" in err and "E.jsonl" in err and "F.jsonl" not in err
    assert (d / "D.jsonl").read_bytes() == orig["D.jsonl"]
    assert (d / "E.jsonl").read_bytes() == orig["E.jsonl"]
    assert len((d / "F.jsonl").read_text().splitlines()) == 1  # identical dup collapsed


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = tmp_path / "repo"
    (r / "data/market/ohlcv").mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (r / "data/market/ohlcv/A.jsonl").write_text(
        _line("2026-01-05") + "\n" + _line("2026-01-02") + "\n" + _line("2026-01-03") + "\n")
    run("add", "-A")
    run("commit", "-qm", "base")
    monkeypatch.setattr(verify, "_PROJECT_ROOT", r)
    return r


def test_verify_passes_after_normalisation_and_fails_on_a_changed_value(repo: Path, capsys) -> None:
    ohlcv = repo / "data/market/ohlcv"
    assert verify.main(["--against", "HEAD", "--dir", str(ohlcv)]) == 1  # control: unsorted fails
    assert norm.main(["--dir", str(ohlcv), "--apply"]) == 0
    assert verify.main(["--against", "HEAD", "--dir", str(ohlcv)]) == 0

    # value change must fail even though the file stays canonical
    lines = (ohlcv / "A.jsonl").read_text().splitlines()
    lines[0] = _line("2026-01-02", 9.9)
    (ohlcv / "A.jsonl").write_text("\n".join(lines) + "\n")
    assert verify.main(["--against", "HEAD", "--dir", str(ohlcv)]) == 1
    assert "differs" in capsys.readouterr().err


def test_verify_unknown_ref_is_unknown_not_healthy(repo: Path) -> None:
    assert verify.main(["--against", "no-such-ref", "--dir", str(repo / "data/market/ohlcv")]) == 2


def test_no_doc_claims_the_store_is_deliberately_out_of_order():
    """Regression: the store is canonical ascending-date since 2026-10-03.

    The units-migration docstring and a merge_rows comment still said line
    order is deliberately preserved / a row keeps its file position, which
    contradicts the writers and invites someone to forbid re-sorting.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    stale = (
        "deliberately out of date order",
        "keeps this row's position in the file",
    )
    offenders = [
        f"{rel}: {phrase!r}"
        for rel in ("scripts/normalise_store_units.py", "engine/ohlcv_ingest.py")
        for phrase in stale
        if phrase in (root / rel).read_text(encoding="utf-8")
    ]
    assert offenders == []
