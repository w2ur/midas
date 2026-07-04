"""Leaderboard page — strategy rankings by return, Sharpe, or max drawdown."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
_PORTFOLIOS_DIR = _ROOT / "data" / "portfolios"
_FACTOR_RESEARCH = _ROOT / "data" / "factor-research.json"

# Streamlit runs pages with ``app/pages`` on ``sys.path``, not the repo root,
# so the ``engine`` package is not importable unless we add the root ourselves.
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.leaderboard import annualized_sharpe, max_drawdown  # noqa: E402

_BASELINE_ID = "coin-flip-baseline"

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Leaderboard — Midas", page_icon="👑", layout="wide")
st.title("Leaderboard")
st.caption("Strategy rankings — who's beating the coin flip?")

# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------


def _load_backtest_results() -> pd.DataFrame | None:
    if not _FACTOR_RESEARCH.exists():
        return None
    try:
        data = json.loads(_FACTOR_RESEARCH.read_text())
        # New shape wraps rows under "results"; legacy shape is a bare list.
        rows = data.get("results", []) if isinstance(data, dict) else data
        if not rows:
            return None
        return pd.DataFrame(rows)
    except Exception:
        return None


def _load_live_snapshots() -> pd.DataFrame | None:
    """Build a summary row per portfolio from snapshots (last vs first value)."""
    if not _PORTFOLIOS_DIR.exists():
        return None

    rows: list[dict] = []
    for portfolio_dir in sorted(_PORTFOLIOS_DIR.iterdir()):
        if not portfolio_dir.is_dir():
            continue
        snapshots_file = portfolio_dir / "snapshots.json"
        if not snapshots_file.exists():
            continue
        try:
            records = json.loads(snapshots_file.read_text())
            if len(records) < 2:
                continue
            df = pd.DataFrame(records)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")

            first_val = df["portfolio_value"].iloc[0]
            last_val = df["portfolio_value"].iloc[-1]
            total_return = (last_val / first_val - 1) if first_val else 0.0

            nav = df["portfolio_value"].tolist()
            drawdown = max_drawdown(nav)
            sharpe = annualized_sharpe(nav)

            rows.append(
                {
                    "id": portfolio_dir.name,
                    "source": "live",
                    "total_return": total_return,
                    "max_drawdown": drawdown if drawdown is not None else float("nan"),
                    "sharpe": sharpe if sharpe is not None else float("nan"),
                }
            )
        except Exception:
            continue

    if not rows:
        return None
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Merge sources
# ---------------------------------------------------------------------------

backtest_df = _load_backtest_results()
live_df = _load_live_snapshots()

frames: list[pd.DataFrame] = []

if backtest_df is not None and not backtest_df.empty:
    # Normalise to the columns we care about.
    keep = ["id", "total_return", "sharpe", "max_drawdown"]
    available = [c for c in keep if c in backtest_df.columns]
    bt = backtest_df[available].copy()
    if "id" not in bt.columns and "name" in backtest_df.columns:
        bt["id"] = backtest_df["name"]
    bt["source"] = "backtest"
    frames.append(bt)

if live_df is not None and not live_df.empty:
    frames.append(live_df)

if not frames:
    st.info(
        "No data available. "
        "Run backtests (python scripts/run_all_combos.py) or the daily session to generate results."
    )
    st.stop()

combined = pd.concat(frames, ignore_index=True)

# ---------------------------------------------------------------------------
# Metric selector
# ---------------------------------------------------------------------------

metric_options = [
    c for c in ["total_return", "sharpe", "max_drawdown"] if c in combined.columns
]
metric = st.selectbox("Rank by", metric_options)

# For max_drawdown, lower is better — ascending=True.
ascending = metric == "max_drawdown"
ranked = combined.sort_values(
    metric, ascending=ascending, na_position="last"
).reset_index(drop=True)
ranked.index = ranked.index + 1  # 1-based rank

# ---------------------------------------------------------------------------
# Style: highlight baseline row
# ---------------------------------------------------------------------------


def _highlight_baseline(row: pd.Series) -> list[str]:
    id_val = str(row.get("id", ""))
    if _BASELINE_ID in id_val:
        return ["background-color: #3a3a1a; color: #ffff80"] * len(row)
    return [""] * len(row)


# Format numeric columns for display.
display = ranked.copy()
for col in ["total_return", "max_drawdown"]:
    if col in display.columns and pd.api.types.is_numeric_dtype(display[col]):
        display[col] = display[col].apply(lambda v: f"{v:.2%}" if pd.notna(v) else "—")
if "sharpe" in display.columns and pd.api.types.is_numeric_dtype(display["sharpe"]):
    display["sharpe"] = display["sharpe"].apply(
        lambda v: f"{v:.3f}" if pd.notna(v) else "—"
    )

st.caption(f"{len(display)} strategies — 🟡 = coin-flip baseline")

st.dataframe(
    display.style.apply(_highlight_baseline, axis=1),
    use_container_width=True,
)

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.divider()
st.markdown(
    "Made with care by [William](https://william.revah.paris)",
    unsafe_allow_html=True,
)
