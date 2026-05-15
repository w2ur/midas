"""Tests for engine.tickers — registry I/O, idempotent merge, name resolution."""

from pathlib import Path

from engine.tickers import (
    load_registry,
    save_registry,
    merge,
    resolve_name,
)


def test_load_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    assert load_registry(path=tmp_path / "nope.json") == {}


def test_round_trip_preserves_data(tmp_path: Path) -> None:
    reg = {
        "AAPL": {"name": "Apple Inc.", "type": "equity"},
        "VOO": {"name": "Vanguard S&P 500 ETF", "type": "etf"},
    }
    path = tmp_path / "tickers.json"
    save_registry(reg, path=path)
    assert load_registry(path=path) == reg


def test_merge_adds_new_entry() -> None:
    existing = {"AAPL": {"name": "Apple Inc.", "type": "equity"}}
    fresh = {"MSFT": {"name": "Microsoft Corporation", "type": "equity"}}
    out = merge(existing, fresh)
    assert out["AAPL"] == {"name": "Apple Inc.", "type": "equity"}
    assert out["MSFT"] == {"name": "Microsoft Corporation", "type": "equity"}


def test_merge_keeps_existing_when_fresh_name_is_null() -> None:
    existing = {"AAPL": {"name": "Apple Inc.", "type": "equity"}}
    fresh = {"AAPL": {"name": None, "type": "unknown"}}
    out = merge(existing, fresh)
    assert out["AAPL"] == {"name": "Apple Inc.", "type": "equity"}


def test_merge_replaces_existing_when_fresh_name_is_non_null() -> None:
    existing = {"AAPL": {"name": None, "type": "unknown"}}
    fresh = {"AAPL": {"name": "Apple Inc.", "type": "equity"}}
    out = merge(existing, fresh)
    assert out["AAPL"] == {"name": "Apple Inc.", "type": "equity"}


def test_merge_overwrites_existing_when_fresh_name_changes() -> None:
    existing = {"X": {"name": "Old Name", "type": "equity"}}
    fresh = {"X": {"name": "New Name", "type": "equity"}}
    out = merge(existing, fresh)
    assert out["X"] == {"name": "New Name", "type": "equity"}


def test_merge_preserves_keys_only_in_existing() -> None:
    existing = {"AAPL": {"name": "Apple Inc.", "type": "equity"}}
    fresh = {"MSFT": {"name": "Microsoft Corporation", "type": "equity"}}
    out = merge(existing, fresh)
    assert "AAPL" in out
