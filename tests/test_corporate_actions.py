"""Tests for engine.corporate_actions — stock-split detection and share adjustment.

Regression: 4b6b8556 — the committed OHLCV store had 11 tickers whose
pre-split history was never restated after a real split. Nothing detected or
adjusted for it: a position held through a split kept its pre-split share
count against a post-split price, silently mis-valuing the book by the
split ratio.

The thresholds under test are not arbitrary — see the module docstring in
engine/corporate_actions.py for the real numbers (measured against the 11
splits swept in commit 4b6b8556 and live ALV.DE/BMW.DE fetches) that
calibrated them. HON (ratio 0.9535, 4.65% from 1.0) is the closest real
split to the materiality floor; WLN.PA (0.025, 2.23% cluster spread) is the
closest real split to the cluster-tightness ceiling.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from engine.corporate_actions import apply_split, detect_split

_DAY_ZERO = date(2026, 6, 1)


def _frame(closes: list[float], *, start: date = _DAY_ZERO) -> pd.DataFrame:
    """Build a yfinance-shaped single-column ("Close") DataFrame.

    One row per calendar day starting at `start` — detect_split only reads
    the Close column, so the other OHLCV fields real yfinance frames carry
    are irrelevant here (see tests/test_ohlcv_ingest.py's `_yf_frame` for
    the full-field version used elsewhere in the suite).
    """
    idx = pd.DatetimeIndex(
        [pd.Timestamp(start + timedelta(days=i)) for i in range(len(closes))],
        name="Date",
    )
    return pd.DataFrame({"Close": closes}, index=idx)


def _stored(closes: list[float], *, start: date = _DAY_ZERO) -> list[dict]:
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "close": c}
        for i, c in enumerate(closes)
    ]


# ---------------------------------------------------------------------------
# detect_split
# ---------------------------------------------------------------------------


def test_detect_split_finds_a_single_constant_ratio():
    """CRWD's real signature: every overlapping row divided by the same
    factor (measured 0.0% spread across 552 rows in commit 4b6b8556)."""
    fetched_closes = [100.0 + i for i in range(15)]
    stored_closes = [c * 4.0 for c in fetched_closes]
    assert detect_split(
        _stored(stored_closes), _frame(fetched_closes)
    ) == pytest.approx(4.0)


def test_detect_split_ignores_scattered_drift():
    """ALV.DE's real signature: many rows, each at its OWN distinct ratio,
    scattered across a couple of percent — measured 4.20% spread across 24
    rows live. Must never be read as a split."""
    ratios = [0.960 + 0.002 * i for i in range(15)]  # 15 distinct ratios
    fetched_closes = [100.0] * 15
    stored_closes = [f * r for f, r in zip(fetched_closes, ratios)]
    assert detect_split(_stored(stored_closes), _frame(fetched_closes)) is None


def test_detect_split_ignores_an_unchanged_series():
    closes = [100.0 + i for i in range(12)]
    assert detect_split(_stored(closes), _frame(closes)) is None


def test_detect_split_requires_a_handful_of_overlapping_rows():
    """A single wrong row (the sweep's Class-B "Day-1" errors) must never
    read as a split just because it happens to be a clean ratio — there is
    nothing to cluster with only 3 overlapping dates."""
    fetched_closes = [100.0, 101.0, 102.0]
    stored_closes = [c * 4.0 for c in fetched_closes]
    assert detect_split(_stored(stored_closes), _frame(fetched_closes)) is None


def test_detect_split_rejects_a_ratio_too_close_to_one():
    """A tight, consistent 1% shift is still not a split — HON (4.65% from
    1.0) is the closest real split to this floor; a benign 1% systematic
    difference must stay below it."""
    fetched_closes = [100.0 + i for i in range(15)]
    stored_closes = [c * 1.01 for c in fetched_closes]
    assert detect_split(_stored(stored_closes), _frame(fetched_closes)) is None


def test_detect_split_tolerates_low_price_rounding_noise():
    """WLN.PA's real signature: a 40-for-1 reverse split (ratio 0.025) whose
    post-split price is so low that 2-decimal store rounding introduces up
    to 2.23% of spread within the single true cluster — measured live. Must
    still detect, just under the 3% cluster-tolerance ceiling."""
    fetched_closes = [20.0 + i for i in range(15)]  # pre-split, EUR-ish range
    # ratio wobbles between 0.0245 and 0.0250 -> ~2% spread, mirrors WLN.PA
    wobble = [0.0245, 0.0247, 0.0250, 0.0248, 0.0246] * 3
    stored_closes = [f * r for f, r in zip(fetched_closes, wobble)]
    ratio = detect_split(_stored(stored_closes), _frame(fetched_closes))
    assert ratio is not None
    assert ratio == pytest.approx(0.0247, abs=0.001)


def test_detect_split_refuses_two_competing_clusters():
    """Ambiguous evidence (two candidate ratios, not one) must refuse rather
    than guess — mirrors a real artifact found while calibrating these
    thresholds: WLN.PA's full 2016+ history carries an EARLIER, unrelated
    split baked in by the same deep resweep, producing a second cluster
    alongside the one this plan tracks. A false positive here would
    silently multiply a real position by the wrong factor."""
    fetched_closes = [100.0 + i for i in range(20)]
    ratios = [0.097] * 10 + [0.025] * 10
    stored_closes = [f * r for f, r in zip(fetched_closes, ratios)]
    assert detect_split(_stored(stored_closes), _frame(fetched_closes)) is None


def test_detect_split_ignores_adj_close_only_dividend_noise():
    """Yahoo re-bases adj_close after every dividend but leaves raw close
    alone — the pre-flight finding this plan's ledger records. A store row
    carrying a wildly different adj_close (dividend rebasing) must not be
    mistaken for a split when the raw close the detector actually reads
    agrees with the fetched series."""
    closes = [100.0 + i for i in range(15)]
    stored = [
        {"date": r["date"], "close": r["close"], "adj_close": r["close"] * 0.6}
        for r in _stored(closes)
    ]
    assert detect_split(stored, _frame(closes)) is None


# ---------------------------------------------------------------------------
# apply_split
# ---------------------------------------------------------------------------


def test_apply_split_scales_shares_and_preserves_cost_basis():
    positions = [
        {
            "ticker": "CRWD",
            "shares": 3.0,
            "avg_cost": 400.0,
            "date_opened": "2026-05-01",
            "grid_level": 0,
        },
        {
            "ticker": "AAPL",
            "shares": 5.0,
            "avg_cost": 200.0,
            "date_opened": "2026-05-01",
            "grid_level": 0,
        },
    ]
    out = apply_split(positions, "CRWD", 4.0)

    crwd = next(p for p in out if p["ticker"] == "CRWD")
    assert crwd["shares"] == pytest.approx(12.0)
    assert crwd["avg_cost"] == pytest.approx(100.0)
    assert crwd["shares"] * crwd["avg_cost"] == pytest.approx(1200.0)  # basis unchanged
    assert crwd["date_opened"] == "2026-05-01"  # other fields survive untouched
    assert crwd["grid_level"] == 0

    aapl = next(p for p in out if p["ticker"] == "AAPL")
    assert aapl["shares"] == 5.0
    assert aapl["avg_cost"] == 200.0


def test_apply_split_handles_a_reverse_split_with_fractional_shares():
    # WLN.PA went 0.025 — a 40:1 reverse. Fractional shares must survive.
    out = apply_split(
        [{"ticker": "WLN.PA", "shares": 40.0, "avg_cost": 2.0}], "WLN.PA", 0.025
    )
    assert out[0]["shares"] == pytest.approx(1.0)
    assert out[0]["avg_cost"] == pytest.approx(80.0)
    assert out[0]["shares"] * out[0]["avg_cost"] == pytest.approx(80.0)


def test_apply_split_does_not_mutate_the_input():
    positions = [{"ticker": "CRWD", "shares": 3.0, "avg_cost": 400.0}]
    apply_split(positions, "CRWD", 4.0)
    assert positions[0]["shares"] == 3.0
    assert positions[0]["avg_cost"] == 400.0


def test_apply_split_rejects_a_non_positive_ratio():
    positions = [{"ticker": "CRWD", "shares": 3.0, "avg_cost": 400.0}]
    with pytest.raises(ValueError):
        apply_split(positions, "CRWD", 0.0)
    with pytest.raises(ValueError):
        apply_split(positions, "CRWD", -4.0)
