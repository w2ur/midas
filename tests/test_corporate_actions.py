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

import statistics
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


def _frame_from_dates(dates: list[str], closes: list[float]) -> pd.DataFrame:
    """Like `_frame`, but for non-consecutive (real calendar / synthetic
    scattered) dates rather than a daily-sequential run."""
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates], name="Date")
    return pd.DataFrame({"Close": closes}, index=idx)


def _stored_from_dates(dates: list[str], closes: list[float]) -> list[dict]:
    return [{"date": d, "close": c} for d, c in zip(dates, closes)]


# ---------------------------------------------------------------------------
# Real-data fixtures — committed history, no live network calls (CI-safe).
#
# Each row is (date, stored_close, fetched_close), captured once from real
# sources so these run offline and deterministically in CI (which checks out
# with fetch-depth: 1 — a `git show <historical-sha>` at test time would fail
# there, so the pre-sweep CRWD series below was captured via `git show
# 314035e34:data/market/ohlcv/CRWD.jsonl` at authoring time, not re-fetched
# per run).
# ---------------------------------------------------------------------------

# CRWD's real 4-for-1 split (effective 2026-07-02): `stored_close` is the
# pre-sweep raw `close` (from commit 314035e34, before 4b6b8556 corrected
# it); `fetched_close` is the currently-committed store's `close` for the
# same dates, which the task-9 investigation verified matches live Yahoo
# data. 45 real pre-split rows (ratio exactly 4.0) followed by 26 real
# post-split rows (ratio exactly 1.0) — a clean, real positive that also
# exercises the temporal check's ordinary path: prefix drifted, suffix not.
_CRWD_PRE_SWEEP_VS_CURRENT = [
    ("2026-04-23", 445.3900146484375, 111.34750366210938),
    ("2026-04-24", 448.1300048828125, 112.03250122070312),
    ("2026-04-27", 454.6099853515625, 113.65249633789062),
    ("2026-04-28", 454.989990234375, 113.74749755859375),
    ("2026-04-29", 452.3800048828125, 113.09500122070312),
    ("2026-04-30", 445.75, 111.4375),
    ("2026-05-01", 455.6400146484375, 113.91000366210938),
    ("2026-05-04", 469.239990234375, 117.30999755859375),
    ("2026-05-05", 476.5299987792969, 119.13249969482422),
    ("2026-05-06", 468.07000732421875, 117.01750183105469),
    ("2026-05-07", 505.7200012207031, 126.43000030517578),
    ("2026-05-08", 527.77001953125, 131.9425048828125),
    ("2026-05-11", 542.260009765625, 135.56500244140625),
    ("2026-05-12", 546.1799926757812, 136.5449981689453),
    ("2026-05-13", 562.5700073242188, 140.6425018310547),
    ("2026-05-14", 579.9500122070312, 144.9875030517578),
    ("2026-05-15", 594.0800170898438, 148.52000427246094),
    ("2026-05-18", 618.8300170898438, 154.70750427246094),
    ("2026-05-19", 616.8800048828125, 154.22000122070312),
    ("2026-05-20", 650.1099853515625, 162.52749633789062),
    ("2026-05-21", 648.22998046875, 162.0574951171875),
    ("2026-05-22", 663.4600219726562, 165.86500549316406),
    ("2026-05-26", 671.5499877929688, 167.8874969482422),
    ("2026-05-27", 645.3599853515625, 161.33999633789062),
    ("2026-05-28", 671.0, 167.75),
    ("2026-05-29", 731.0, 182.75),
    ("2026-06-01", 782.1699829101562, 195.54249572753906),
    ("2026-06-02", 768.9500122070312, 192.2375030517578),
    ("2026-06-03", 747.6099853515625, 186.90249633789062),
    ("2026-06-04", 719.0900268554688, 179.7725067138672),
    ("2026-06-05", 671.02001953125, 167.7550048828125),
    ("2026-06-08", 658.7899780273438, 164.69749450683594),
    ("2026-06-09", 644.9299926757812, 161.2324981689453),
    ("2026-06-10", 647.739990234375, 161.93499755859375),
    ("2026-06-11", 691.530029296875, 172.88250732421875),
    ("2026-06-12", 682.7999877929688, 170.6999969482422),
    ("2026-06-15", 692.9099731445312, 173.2274932861328),
    ("2026-06-16", 679.489990234375, 169.87249755859375),
    ("2026-06-17", 682.9600219726562, 170.74000549316406),
    ("2026-06-18", 684.8599853515625, 171.21499633789062),
    ("2026-06-22", 675.4400024414062, 168.86000061035156),
    ("2026-06-23", 680.9199829101562, 170.22999572753906),
    ("2026-06-24", 673.02001953125, 168.2550048828125),
    ("2026-06-25", 678.6500244140625, 169.66250610351562),
    ("2026-06-26", 701.0900268554688, 175.2725067138672),
    ("2026-06-29", 742.9099731445312, 185.7274932861328),
    ("2026-07-01", 772.739990234375, 193.18499755859375),
    ("2026-07-02", 193.97999572753906, 193.97999572753906),
    ("2026-07-06", 199.3800048828125, 199.3800048828125),
    ("2026-07-07", 194.6199951171875, 194.6199951171875),
    ("2026-07-08", 191.1199951171875, 191.1199951171875),
    ("2026-07-09", 198.39999389648438, 198.39999389648438),
    ("2026-07-10", 187.17999267578125, 187.17999267578125),
    ("2026-07-13", 187.91000366210938, 187.91000366210938),
    ("2026-07-14", 210.72999572753906, 210.72999572753906),
    ("2026-07-15", 206.77000427246094, 206.77000427246094),
    ("2026-07-16", 203.75999450683594, 203.75999450683594),
    ("2026-07-17", 203.0800018310547, 203.0800018310547),
    ("2026-07-20", 198.49000549316406, 198.49000549316406),
    ("2026-07-21", 191.14999389648438, 191.14999389648438),
    ("2026-07-22", 188.4199981689453, 188.4199981689453),
    ("2026-07-23", 183.4199981689453, 183.4199981689453),
    ("2026-07-24", 183.27999877929688, 183.27999877929688),
    ("2026-07-27", 180.11000061035156, 180.11000061035156),
    ("2026-07-28", 181.8000030517578, 181.8000030517578),
    ("2026-07-29", 179.3800048828125, 179.3800048828125),
    ("2026-07-30", 185.22000122070312, 185.22000122070312),
    ("2026-07-31", 190.86000061035156, 190.86000061035156),
    ("2026-08-03", 202.5399932861328, 202.5399932861328),
    ("2026-08-04", 211.22000122070312, 211.22000122070312),
    ("2026-08-05", 209.86000061035156, 209.86000061035156),
]

# Real Class-D drifted rows for SIE.DE (currently HELD by sharp-shooter-eur —
# the case a reviewer named as a concrete risk), ALV.DE, and BMW.DE. Task 2
# deliberately left this class unswept (mechanism not understood — possibly
# Yahoo restating its own history; see the plan ledger). Captured live
# (auto_adjust=False) against the currently-committed store at authoring
# time. Real spread (SIE.DE ~4.9%, ALV.DE 4.20%, BMW.DE 6.08%) is well past
# _CLUSTER_TOLERANCE on its own — these pin the negative on real data
# regardless of the temporal check, guarding against a future threshold
# loosening reintroducing a false positive on a real, currently-held ticker.
_SIE_DE_DRIFTED = [
    ("2021-12-07", 149.33999633789062, 153.10000610351562),
    ("2022-04-21", 119.76000213623047, 123.0),
    ("2022-04-27", 113.0199966430664, 114.0999984741211),
    ("2022-05-06", 116.58000183105469, 114.63999938964844),
    ("2022-05-13", 113.9000015258789, 116.08000183105469),
    ("2022-05-25", 114.19999694824219, 114.81999969482422),
    ("2022-05-27", 118.5199966430664, 121.86000061035156),
    ("2022-05-30", 118.5199966430664, 124.81999969482422),
    ("2022-06-02", 122.58000183105469, 125.23999786376953),
    ("2022-07-12", 96.80000305175781, 97.62999725341797),
    ("2022-08-29", 102.9000015258789, 102.22000122070312),
    ("2023-01-27", 144.05999755859375, 144.5),
    ("2024-11-28", 178.97999572753906, 180.8800048828125),
    ("2024-12-10", 194.13999938964844, 192.8800048828125),
    ("2024-12-30", 189.60000610351562, 188.55999755859375),
    ("2025-01-08", 193.5399932861328, 195.63999938964844),
    ("2025-01-21", 201.8000030517578, 204.14999389648438),
    ("2025-02-10", 207.4499969482422, 211.14999389648438),
    ("2025-03-24", 228.85000610351562, 225.35000610351562),
    ("2025-04-25", 200.64999389648438, 206.8000030517578),
    ("2025-05-14", 225.14999389648438, 224.0500030517578),
    ("2025-07-16", 218.3000030517578, 217.14999389648438),
    ("2025-09-22", 228.39999389648438, 227.0500030517578),
    ("2025-09-30", 226.1999969482422, 229.1999969482422),
    ("2025-11-07", 241.60000610351562, 237.3000030517578),
    ("2025-12-12", 238.85000610351562, 237.0500030517578),
    ("2025-12-17", 237.5, 232.64999389648438),
    ("2025-12-18", 237.5, 235.8000030517578),
]

_ALV_DE_DRIFTED = [
    ("2021-12-07", 202.9499969482422, 205.6999969482422),
    ("2022-04-21", 220.3000030517578, 222.39999389648438),
    ("2022-04-27", 213.3000030517578, 212.25),
    ("2022-05-06", 199.75999450683594, 195.6999969482422),
    ("2022-05-13", 198.33999633789062, 200.39999389648438),
    ("2022-05-27", 198.8000030517578, 200.1999969482422),
    ("2022-05-30", 198.8000030517578, 199.60000610351562),
    ("2022-06-02", 193.89999389648438, 194.74000549316406),
    ("2022-07-12", 178.27999877929688, 179.3800048828125),
    ("2024-11-01", 289.3999938964844, 291.6000061035156),
    ("2024-11-28", 287.8999938964844, 289.29998779296875),
    ("2025-01-21", 306.1000061035156, 304.5),
    ("2025-01-24", 310.79998779296875, 308.8999938964844),
    ("2025-02-10", 318.3999938964844, 320.0),
    ("2025-03-17", 351.6000061035156, 354.0),
    ("2025-06-09", 355.29998779296875, 350.6000061035156),
    ("2025-07-16", 339.70001220703125, 342.1000061035156),
    ("2025-07-21", 345.6000061035156, 343.6000061035156),
    ("2025-08-14", 368.70001220703125, 376.6000061035156),
    ("2025-11-07", 353.29998779296875, 352.0),
    ("2025-11-13", 361.0, 363.3999938964844),
    ("2025-12-15", 380.3999938964844, 383.29998779296875),
    ("2025-12-17", 383.29998779296875, 385.6000061035156),
    ("2025-12-18", 383.29998779296875, 385.8999938964844),
]

_BMW_DE_DRIFTED = [
    ("2021-12-07", 88.41999816894531, 90.83999633789062),
    ("2022-04-21", 79.19999694824219, 80.08000183105469),
    ("2022-04-22", 79.19999694824219, 78.5199966430664),
    ("2022-04-27", 76.2699966430664, 76.56999969482422),
    ("2022-05-06", 78.02999877929688, 79.05999755859375),
    ("2022-05-13", 75.77999877929688, 77.66000366210938),
    ("2022-05-25", 77.79000091552734, 78.55000305175781),
    ("2022-05-27", 80.25, 80.7699966430664),
    ("2022-05-30", 80.25, 81.4800033569336),
    ("2022-06-02", 82.33000183105469, 83.81999969482422),
    ("2022-06-03", 82.33000183105469, 82.93000030517578),
    ("2022-07-12", 73.79000091552734, 74.41999816894531),
    ("2023-01-27", 92.3499984741211, 92.94000244140625),
    ("2024-11-01", 72.31999969482422, 73.16000366210938),
    ("2024-11-28", 68.4000015258789, 68.91999816894531),
    ("2024-12-10", 79.19999694824219, 79.68000030517578),
    ("2025-01-21", 79.26000213623047, 77.83999633789062),
    ("2025-01-24", 77.37999725341797, 78.83999633789062),
    ("2025-02-10", 76.37999725341797, 77.04000091552734),
    ("2025-03-17", 82.0999984741211, 83.54000091552734),
    ("2025-03-24", 79.16000366210938, 79.44000244140625),
    ("2025-05-14", 83.9800033569336, 82.30000305175781),
    ("2025-06-05", 77.23999786376953, 76.9800033569336),
    ("2025-06-09", 76.68000030517578, 77.0199966430664),
    ("2025-07-16", 85.05999755859375, 84.45999908447266),
    ("2025-07-21", 83.5199966430664, 84.5),
    ("2025-07-23", 83.23999786376953, 86.69999694824219),
    ("2025-09-22", 83.26000213623047, 82.33999633789062),
    ("2025-11-07", 84.30000305175781, 86.13999938964844),
    ("2025-12-15", 96.13999938964844, 95.18000030517578),
    ("2025-12-17", 94.26000213623047, 93.4800033569336),
    ("2025-12-18", 94.26000213623047, 93.05999755859375),
]


def _detect_from_triples(rows: list[tuple[str, float, float]]) -> float | None:
    dates = [d for d, _, _ in rows]
    stored = _stored_from_dates(dates, [s for _, s, _ in rows])
    fetched = _frame_from_dates(dates, [f for _, _, f in rows])
    return detect_split(stored, fetched)


def test_detect_split_finds_crwd_real_pre_sweep_split():
    assert _detect_from_triples(_CRWD_PRE_SWEEP_VS_CURRENT) == pytest.approx(4.0)


@pytest.mark.parametrize(
    "name,rows",
    [
        ("SIE.DE", _SIE_DE_DRIFTED),
        ("ALV.DE", _ALV_DE_DRIFTED),
        ("BMW.DE", _BMW_DE_DRIFTED),
    ],
)
def test_detect_split_real_class_d_drift_never_detected(name, rows):
    assert _detect_from_triples(rows) is None


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


def test_detect_split_refuses_a_tight_but_temporally_scattered_cluster():
    """The exact shape a code reviewer flagged as a structural gap: a ratio
    cluster tight enough to clear _CLUSTER_TOLERANCE (~0.2% internal spread)
    and far enough from 1.0 to clear _MATERIALITY_FLOOR (~5% deviation), but
    scattered THROUGHOUT the history — interleaved with unchanged rows on
    both sides — rather than confined to a single prefix ending at one
    transition date.

    Before the temporal contiguity check was added, this shape passed both
    statistical gates and returned a ratio: exactly the false positive that
    would silently multiply a real position by a bogus factor, on a symbol
    that merely happened to have unusually tight scattered drift. It is
    dangerous specifically BECAUSE ratio statistics alone cannot tell it
    apart from a real split — only the temporal signature (drift confined to
    one prefix, zero drift after a single transition date) can. This is a
    synthetic worst case, not real data — real Class-D drift (SIE.DE/ALV.DE/
    BMW.DE, pinned above) is both far less tight (4.2-6.1% spread) AND
    already fails on temporal grounds, but a future threshold retune could
    someday narrow that first margin, which is exactly why the temporal
    check must not be the only thing standing between a retune and a
    corrupted position."""
    base = date(2021, 1, 4)
    # ~0.2% internal wobble around a ~5% ratio — tight enough to have
    # cleared _CLUSTER_TOLERANCE (3%) on its own before this fix.
    wobble = [-0.0010, -0.0005, 0.0, 0.0005, 0.0010, 0.0007, -0.0007, 0.0003]

    dates: list[str] = []
    fetched_closes: list[float] = []
    stored_closes: list[float] = []
    day_offset = 0
    for i in range(32):
        for _ in range(3):  # undrifted context rows around each drifted one
            d = base + timedelta(days=day_offset)
            dates.append(d.isoformat())
            close = 100.0 + day_offset * 0.1
            fetched_closes.append(close)
            stored_closes.append(close)  # ratio exactly 1.0 — unchanged
            day_offset += 7
        d = base + timedelta(days=day_offset)
        dates.append(d.isoformat())
        close = 100.0 + day_offset * 0.1
        fetched_closes.append(close)
        stored_closes.append(close * (1.05 + wobble[i % len(wobble)]))
        day_offset += 7

    ratios = [s / f for s, f in zip(stored_closes, fetched_closes)]
    drifted_ratios = [r for r in ratios if abs(r - 1.0) > 0.003]
    assert len(drifted_ratios) == 32
    spread = (max(drifted_ratios) - min(drifted_ratios)) / statistics.median(
        drifted_ratios
    )
    # Sanity: confirm this shape really does clear the two PRE-EXISTING
    # statistical gates on its own — the point is that ratio statistics
    # alone are not enough, not that this shape is somehow unrealistic.
    assert spread < 0.03  # would have cleared _CLUSTER_TOLERANCE
    assert abs(statistics.median(drifted_ratios) - 1.0) > 0.03  # and _MATERIALITY_FLOOR

    stored = _stored_from_dates(dates, stored_closes)
    fetched = _frame_from_dates(dates, fetched_closes)
    assert detect_split(stored, fetched) is None


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
