"""The coin flip advances from a persisted state over new dates only.

Background (plan subtask 1.6, owner decision D4, 2026-10-05). The coin flip
was a `bt` backtest recomputed from day one on every session, with one
`random.Random(seed)` reused across every day. A change in the candidate list
on any day (a universe refresh) or in any close (a corporate-action rescale)
changed every later pick, and integer shares made the series sensitive to the
scale a price is quoted in. The append-only merge kept each published row as
its session computed it, so the published curve became a splice of paths with
seams between rows written by different sessions (the largest measured at
+25.95% in one day, `yolo-sapiens-usd` 2026-09-20 -> 09-21).

Now each agent's coin flip keeps a state (`data/baselines/<agent>/state/
coinflip.json`): the date it was last advanced to, its cash and its holdings,
each `{shares, mark_date, mark_close}`. A session values the holdings by the
ratio of the current store's closes to the recorded mark, repicks with a seed
of `(agent, date)` over the sorted candidates of that date, and appends the new
dates. No published row is ever recomputed.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine.baselines import (
    CoinFlipHolding,
    CoinFlipState,
    advance_coin_flip,
    build_all_baselines,
    coin_flip_state_path,
    compute_coin_flip,
    init_coin_flip_state,
    load_coin_flip_state,
)
from engine.config import get_config
from engine.selectors.random_seeded import make_seed

_AGENT = "probe-agent"


def _store(ticker: str, rows: list[tuple[str, float]]) -> None:
    ohlcv = get_config().ohlcv_dir
    ohlcv.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"date": d, "close": c}) for d, c in rows]
    (ohlcv / f"{ticker}.jsonl").write_text("\n".join(lines) + "\n")


def _days(start: date, n: int) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


_START = date(2026, 1, 1)
_BASES = {"AAA": 116.0, "BBB": 240.0, "CCC": 37.0, "DDD": 8.5, "EEE": 61.0}


def _seed_store(n_days: int = 12, scale: dict[str, float] | None = None) -> None:
    scale = scale or {}
    for k, (ticker, base) in enumerate(_BASES.items()):
        _store(
            ticker,
            [
                (d, base * (1 + 0.04 * (((i * (k + 1)) % 7) - 3)) * scale.get(ticker, 1.0))
                for i, d in enumerate(_days(_START, n_days))
            ],
        )


def _series_path() -> Path:
    return get_config().baselines_dir / _AGENT / "coinflip.json"


def _advance(to: date, tickers=None, max_positions: int = 2):
    return advance_coin_flip(
        agent_id=_AGENT,
        tickers=list(tickers or _BASES),
        currency="EUR",
        max_positions=max_positions,
        series_path=_series_path(),
        from_date=_START,
        to_date=to,
    )


def _rows() -> list[dict]:
    return json.loads(_series_path().read_text())


# ---------------------------------------------------------------------------
# Fresh path and the semantics kept from the bt pipeline
# ---------------------------------------------------------------------------


def test_a_fresh_path_starts_at_initial_capital_and_holds_whole_shares(midas_data_root):
    _seed_store()
    rows = compute_coin_flip(_AGENT, list(_BASES), "EUR", 2, _START, _START + timedelta(days=5))
    assert [r["date"] for r in rows] == _days(_START, 6)
    assert rows[0]["portfolio_value"] == pytest.approx(get_config().initial_capital)
    for r in rows:
        assert r["portfolio_value"] == pytest.approx(r["cash"] + r["positions_value"])
        assert r["currency"] == "EUR"


def test_each_day_repicks_with_a_seed_of_agent_and_date(midas_data_root):
    """Picks are independent of earlier draws and of universe history: the pick
    on a day is the `(agent, date)`-seeded sample of that day's sorted
    candidates, whatever order the universe lists them in."""
    _seed_store()
    orders = [list(_BASES), list(reversed(list(_BASES))), ["CCC", "AAA", "EEE", "BBB", "DDD"]]
    for offset in range(10):
        day = _START + timedelta(days=offset)
        expected = random.Random(make_seed(_AGENT, day.isoformat())).sample(sorted(_BASES), 2)
        for order in orders:
            state = init_coin_flip_state(_AGENT, order, 2, day, 10_000.0)
            assert sorted(state.holdings) == sorted(expected), (day, order)


def test_weights_are_equal_and_capped_at_one_over_n(midas_data_root):
    """Fewer candidates than max_positions leaves the residue in cash (LimitWeights(1/n))."""
    _store("AAA", [(d, 10.0) for d in _days(_START, 3)])
    state = init_coin_flip_state(_AGENT, ["AAA"], 4, _START, 10_000.0)
    assert state.holdings["AAA"].shares == 250  # 10k / 4 / 10
    assert state.cash == pytest.approx(7_500.0)


def test_a_ticker_is_a_candidate_only_from_its_first_close(midas_data_root):
    _store("AAA", [(d, 10.0) for d in _days(_START, 5)])
    _store("LATE", [(d, 10.0) for d in _days(_START + timedelta(days=3), 2)])
    state = init_coin_flip_state(_AGENT, ["AAA", "LATE"], 2, _START, 10_000.0)
    assert set(state.holdings) == {"AAA"}


# ---------------------------------------------------------------------------
# Advancing: new dates only, published rows never move
# ---------------------------------------------------------------------------


def test_the_first_build_writes_the_series_then_the_state(midas_data_root):
    _seed_store()
    result = _advance(_START + timedelta(days=4))
    assert result.appended == 5 and result.concerns == []
    state = load_coin_flip_state(coin_flip_state_path(_series_path()))
    assert state.date == _rows()[-1]["date"]
    assert state.portfolio_value == _rows()[-1]["portfolio_value"]


def test_an_advance_appends_new_dates_and_leaves_published_rows_byte_identical(
    midas_data_root,
):
    _seed_store()
    _advance(_START + timedelta(days=4))
    before = _series_path().read_text()
    result = _advance(_START + timedelta(days=7))
    assert result.appended == 3
    after = _series_path().read_text()
    assert json.loads(after)[:5] == json.loads(before)


def test_a_same_day_rerun_appends_nothing_and_writes_nothing(midas_data_root):
    _seed_store()
    _advance(_START + timedelta(days=4))
    series = _series_path().read_text()
    state = coin_flip_state_path(_series_path()).read_text()
    result = _advance(_START + timedelta(days=4))
    assert result.appended == 0 and result.concerns == []
    assert _series_path().read_text() == series
    assert coin_flip_state_path(_series_path()).read_text() == state


def test_a_universe_swap_changes_no_published_row(midas_data_root):
    _seed_store()
    _advance(_START + timedelta(days=5))
    published = _rows()
    swapped = ["AAA", "CCC", "EEE", "ZZZ-NEW"]
    _store("ZZZ-NEW", [(d, 3.0) for d in _days(_START, 12)])
    result = _advance(_START + timedelta(days=8), tickers=swapped)
    assert result.concerns == []
    assert _rows()[: len(published)] == published


def test_a_store_rescale_changes_no_published_row_and_not_the_next_value(
    midas_data_root,
):
    """Review 2 MUST 2: holdings are valued by a ratio, so a constant rescale of a
    held symbol's whole history cancels. The first new row matches the
    un-rescaled run to 1e-9; only the repick on it sizes at the new scale."""
    import shutil

    def run(rescale: bool) -> tuple[list[dict], list[dict]]:
        shutil.rmtree(get_config().baselines_dir, ignore_errors=True)
        _seed_store()
        _advance(_START + timedelta(days=4))
        published = _rows()
        held = sorted(load_coin_flip_state(coin_flip_state_path(_series_path())).holdings)
        assert held, "the fixture must hold something for the rescale to bite"
        if rescale:
            _seed_store(scale={t: 2.0 for t in held})
        _advance(_START + timedelta(days=5))
        return published, _rows()

    published, plain = run(rescale=False)
    published_again, rescaled = run(rescale=True)
    assert published_again == published
    assert rescaled[: len(published)] == published
    assert rescaled[-1]["portfolio_value"] == pytest.approx(
        plain[-1]["portfolio_value"], rel=1e-9, abs=0
    )


# ---------------------------------------------------------------------------
# Missing prices (review 2 MUST 3)
# ---------------------------------------------------------------------------


def _held_state(ticker: str, shares: int, mark_date: str, mark_close: float, cash=0.0):
    return CoinFlipState(
        date=mark_date,
        portfolio_value=cash + shares * mark_close,
        cash=cash,
        holdings={ticker: CoinFlipHolding(shares, mark_date, mark_close)},
    )


def _seed_state(state: CoinFlipState) -> None:
    from engine.baselines import write_coin_flip_state

    path = _series_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "date": state.date,
                    "portfolio_value": state.portfolio_value,
                    "cash": state.cash,
                    "positions_value": state.portfolio_value - state.cash,
                    "currency": "EUR",
                }
            ]
        )
    )
    write_coin_flip_state(coin_flip_state_path(path), state, _AGENT)


def test_a_missing_close_on_the_day_values_at_the_last_close_before_it(midas_data_root):
    """(a) No close at d: the last close on or before d in the current store."""
    _store("AAA", [("2026-01-01", 10.0), ("2026-01-02", 12.0)])  # nothing on 01-03
    _seed_state(_held_state("AAA", 100, "2026-01-01", 10.0))
    result = _advance(date(2026, 1, 3), tickers=["AAA"], max_positions=1)
    assert result.concerns == []
    assert _rows()[-1]["portfolio_value"] == pytest.approx(1_200.0)


def test_a_held_file_that_is_gone_holds_at_its_mark_and_says_so(midas_data_root, capsys):
    """(b) The file is gone: hold at mark_close, one concern naming agent and
    ticker, and the holding stays in the book."""
    _store("BBB", [(d, 5.0) for d in _days(_START, 5)])
    _seed_state(_held_state("GONE", 100, "2026-01-01", 10.0, cash=500.0))
    result = _advance(date(2026, 1, 4), tickers=["BBB"], max_positions=1)
    warns = [l for l in capsys.readouterr().out.splitlines() if "[WARN]" in l]
    assert len(warns) == 1 and _AGENT in warns[0] and "GONE" in warns[0]
    assert len(result.concerns) == 1
    assert [r["portfolio_value"] for r in _rows()[1:]] == [1_500.0] * 3
    state = load_coin_flip_state(coin_flip_state_path(_series_path()))
    assert state.holdings["GONE"] == CoinFlipHolding(100, "2026-01-01", 10.0)
    # The rest of the book is still repicked: BBB bought from the cash.
    assert state.holdings["BBB"].shares == 100


def test_a_held_file_with_no_close_before_its_mark_holds_at_its_mark(midas_data_root, capsys):
    """(b), second shape: the file survives but its history now starts after the mark."""
    _store("AAA", [("2026-01-03", 99.0), ("2026-01-04", 99.0)])
    _seed_state(_held_state("AAA", 10, "2026-01-01", 10.0))
    _advance(date(2026, 1, 4), tickers=["AAA"], max_positions=1)
    assert "[WARN]" in capsys.readouterr().out
    assert _rows()[-1]["portfolio_value"] == pytest.approx(100.0)


def test_a_mark_row_withdrawn_from_the_store_holds_at_its_mark_and_says_so(
    midas_data_root, capsys
):
    """Review fix round 1: the close a holding was marked at must still be in
    the store on its own date. If that row is withdrawn between two advances,
    the last close before it is a different price, and valuing from it would
    mis-value the holding by the move between the two dates with no concern.
    It is case (b): held at its mark, one concern naming agent and ticker."""
    _store("AAA", [("2026-01-01", 10.0), ("2026-01-02", 20.0)])
    _seed_state(_held_state("AAA", 100, "2026-01-02", 20.0))
    # The 01-02 row is withdrawn; 01-03 lands at 21 (the old code would value
    # 100 x 20 x 21 / 10 = 4,200, a +110% move that never happened).
    _store("AAA", [("2026-01-01", 10.0), ("2026-01-03", 21.0)])
    result = _advance(date(2026, 1, 3), tickers=["AAA"], max_positions=1)
    warns = [l for l in capsys.readouterr().out.splitlines() if "[WARN]" in l]
    assert len(warns) == 1 and _AGENT in warns[0] and "AAA" in warns[0]
    assert len(result.concerns) == 1
    assert _rows()[-1]["portfolio_value"] == pytest.approx(2_000.0)
    state = load_coin_flip_state(coin_flip_state_path(_series_path()))
    assert state.holdings["AAA"] == CoinFlipHolding(100, "2026-01-02", 20.0)


def _suspend(symbol: str, status: str = "suspended") -> None:
    path = get_config().ohlcv_dir.parent / "instrument_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "instruments": {
                    symbol: {
                        "status": status,
                        "since": "2026-01-01",
                        "source": "human",
                        "reason": "test",
                    }
                },
            }
        )
    )


@pytest.mark.parametrize("status", ["suspended", "delisted"])
def test_a_suspended_or_delisted_symbol_is_never_a_candidate(midas_data_root, status):
    """(c) Excluded from candidates."""
    for t in ("AAA", "BAD"):
        _store(t, [(d, 10.0) for d in _days(_START, 3)])
    _suspend("BAD", status)
    for offset in range(3):
        state = init_coin_flip_state(_AGENT, ["AAA", "BAD"], 2, _START + timedelta(days=offset), 1e4)
        assert set(state.holdings) == {"AAA"}


def test_a_held_suspended_symbol_is_valued_and_kept(midas_data_root):
    """(c) A held one is valued per (a)/(b) and stays in the book: it cannot be traded."""
    _store("BAD", [("2026-01-01", 10.0), ("2026-01-02", 11.0)])
    _store("AAA", [(d, 5.0) for d in _days(_START, 4)])
    _suspend("BAD")
    _seed_state(_held_state("BAD", 100, "2026-01-01", 10.0))
    _advance(date(2026, 1, 3), tickers=["AAA", "BAD"], max_positions=2)
    assert [r["portfolio_value"] for r in _rows()[1:]] == pytest.approx([1_100.0, 1_100.0])
    state = load_coin_flip_state(coin_flip_state_path(_series_path()))
    assert state.holdings["BAD"].shares == 100
    assert state.cash == pytest.approx(0.0)


def test_an_unreadable_registry_fails_closed_and_says_so(midas_data_root, capsys):
    _store("AAA", [(d, 10.0) for d in _days(_START, 3)])
    (get_config().ohlcv_dir.parent / "instrument_status.json").write_text("{broken")
    result = _advance(date(2026, 1, 2), tickers=["AAA"], max_positions=1)
    assert any("instrument status" in c for c in result.concerns)
    assert "[WARN]" in capsys.readouterr().out
    assert all(r["positions_value"] == 0.0 for r in _rows())


# ---------------------------------------------------------------------------
# Integrity: the first new row chains from the state date
# ---------------------------------------------------------------------------


def test_a_series_without_a_state_is_not_advanced(midas_data_root, capsys):
    _seed_store()
    _advance(_START + timedelta(days=3))
    coin_flip_state_path(_series_path()).unlink()
    before = _series_path().read_text()
    result = _advance(_START + timedelta(days=6))
    assert result.appended == 0 and len(result.concerns) == 1
    assert "[WARN]" in capsys.readouterr().out
    assert _series_path().read_text() == before
    _assert_names_the_reinit_remedy(result.concerns[0])


def _assert_names_the_reinit_remedy(concern: str) -> None:
    """Review I2: the refusal says how to recover, and what recovering costs."""
    assert "scripts/init_coinflip_state.py --force" in concern
    assert "chore(data):" in concern and "its own" in concern
    assert "new seam" in concern and "METHODOLOGY" in concern


def test_a_state_behind_the_series_is_not_advanced(midas_data_root, capsys, monkeypatch):
    """A run that wrote the series but died before the state: the series is
    written first, so the next run sees the mismatch and recomputes nothing."""
    import engine.baselines as baselines

    _seed_store()
    _advance(_START + timedelta(days=3))

    def boom(*a, **k):
        raise RuntimeError("killed between the two writes")

    monkeypatch.setattr(baselines, "write_coin_flip_state", boom)
    with pytest.raises(RuntimeError):
        _advance(_START + timedelta(days=5))
    monkeypatch.undo()
    before = _series_path().read_text()
    result = _advance(_START + timedelta(days=7))
    assert result.appended == 0 and len(result.concerns) == 1
    assert _series_path().read_text() == before


def test_a_state_whose_value_differs_from_its_row_is_not_advanced(midas_data_root):
    _seed_store()
    _advance(_START + timedelta(days=3))
    path = coin_flip_state_path(_series_path())
    doc = json.loads(path.read_text())
    doc["portfolio_value"] += 1.0
    path.write_text(json.dumps(doc))
    result = _advance(_START + timedelta(days=6))
    assert result.appended == 0 and len(result.concerns) == 1
    _assert_names_the_reinit_remedy(result.concerns[0])


def test_a_state_without_a_series_is_not_advanced(midas_data_root):
    _seed_store()
    _advance(_START + timedelta(days=3))
    _series_path().unlink()
    result = _advance(_START + timedelta(days=6))
    assert result.appended == 0 and len(result.concerns) == 1
    assert not _series_path().exists()


def test_an_unreadable_state_is_not_advanced(midas_data_root):
    _seed_store()
    _advance(_START + timedelta(days=3))
    coin_flip_state_path(_series_path()).write_text("{not json")
    result = _advance(_START + timedelta(days=6))
    assert result.appended == 0 and len(result.concerns) == 1


# ---------------------------------------------------------------------------
# Restart invariance (Hypothesis, `midas` profile)
# ---------------------------------------------------------------------------

_TICKERS = ["P", "Q", "R", "S"]


@given(
    paths=st.lists(
        st.lists(
            st.floats(min_value=0.5, max_value=500.0, allow_nan=False, allow_infinity=False),
            min_size=9,
            max_size=9,
        ),
        min_size=len(_TICKERS),
        max_size=len(_TICKERS),
    ),
    gaps=st.lists(st.booleans(), min_size=9, max_size=9),
    n=st.integers(min_value=1, max_value=7),
    max_positions=st.integers(min_value=0, max_value=5),
)
def test_n_days_in_one_call_equal_n_one_day_calls(
    midas_data_root, paths, gaps, n, max_positions
):
    """Advancing N days in one call equals N one-day calls from the persisted state."""
    import shutil

    days = _days(_START, 9)
    for ticker, closes in zip(_TICKERS, paths):
        # A gap (no bar) on a day exercises the last-close-on-or-before rule.
        _store(ticker, [(d, c) for d, c, g in zip(days, closes, gaps) if not g or d == days[0]])
    base = get_config().baselines_dir
    shutil.rmtree(base, ignore_errors=True)

    def run(to: date):
        return _advance(to, tickers=_TICKERS, max_positions=max_positions)

    run(_START)
    run(_START + timedelta(days=n))
    one_call = (_series_path().read_text(), coin_flip_state_path(_series_path()).read_text())

    shutil.rmtree(base)
    run(_START)
    for k in range(1, n + 1):
        run(_START + timedelta(days=k))
    many_calls = (_series_path().read_text(), coin_flip_state_path(_series_path()).read_text())
    assert one_call == many_calls


# ---------------------------------------------------------------------------
# The build: both writers go through it, restatement is refused
# ---------------------------------------------------------------------------


def _desk_universes(cfg, tickers) -> dict[str, list[str]]:
    return {
        aid: tickers for aid in cfg.trading_roster if cfg.roster[aid].benchmark is not None
    }


def _seed_benchmarks(cfg, days: list[str]) -> None:
    for aid in cfg.trading_roster:
        spec = cfg.roster[aid].benchmark
        if spec is not None and spec.ticker != "EUR_CASH_FLAT":
            _store(spec.ticker, [(d, 100.0 + i) for i, d in enumerate(days)])
    _store(cfg.global_reference.ticker, [(d, 100.0 + i) for i, d in enumerate(days)])


def test_a_coinflip_concern_is_counted_in_the_build(midas_data_root, capsys):
    """Review M2: a coin flip that cannot be advanced is a concern of the
    build, in its totals and its aggregate line, not only a stray [WARN]."""
    cfg = get_config()
    days = _days(_START, 10)
    _seed_benchmarks(cfg, days)
    _seed_store(n_days=10)
    universes = _desk_universes(cfg, ["AAA", "BBB", "CCC"])
    build_all_baselines(universes, _START, _START + timedelta(days=5))
    agent = next(iter(universes))
    coin_flip_state_path(cfg.baselines_dir / agent / "coinflip.json").unlink()
    capsys.readouterr()

    totals = build_all_baselines(universes, _START, _START + timedelta(days=9))

    out = capsys.readouterr().out
    assert totals.concern == 1
    assert "[WARN] baselines: 1 concern(s)" in out
    assert "coin flip" in out.split("[WARN] baselines:")[1]


@pytest.mark.parametrize("scope", [{"coinflip"}, {"goldfinger/coinflip"}])
def test_a_coinflip_restatement_scope_is_refused(midas_data_root, scope):
    with pytest.raises(ValueError, match="coin flip"):
        build_all_baselines({}, _START, _START, restate_series=scope, changelog_entry="x")


def test_a_replay_over_a_universe_refresh_gives_no_coinflip_concern(midas_data_root, capsys):
    """The 2026-09-26 shape: `3EUS.L` left a universe for `3EUS.MI` and every
    later coin-flip path moved. Now the refresh changes candidates from the
    next new date on and nothing published moves."""
    cfg = get_config()
    days = _days(_START, 10)
    _seed_benchmarks(cfg, days)
    _seed_store(n_days=10)
    _store("3EUS.MI", [(d, 4.0 + i / 10) for i, d in enumerate(days[3:])])
    build_all_baselines(_desk_universes(cfg, ["AAA", "BBB", "CCC"]), _START, _START + timedelta(days=5))
    published = {
        aid: (cfg.baselines_dir / aid / "coinflip.json").read_text()
        for aid in _desk_universes(cfg, [])
    }
    capsys.readouterr()
    build_all_baselines(
        _desk_universes(cfg, ["AAA", "BBB", "3EUS.MI", "DDD"]), _START, _START + timedelta(days=9)
    )
    out = capsys.readouterr().out
    assert "[WARN]" not in out
    for aid, before in published.items():
        rows = json.loads((cfg.baselines_dir / aid / "coinflip.json").read_text())
        assert rows[:6] == json.loads(before)
        assert len(rows) == 10
