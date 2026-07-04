"""Overview page — performance chart across all live portfolios and backtest summary."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
_PORTFOLIOS_DIR = _ROOT / "data" / "portfolios"
_FACTOR_RESEARCH = _ROOT / "data" / "factor-research.json"

# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Overview — Midas", page_icon="👑", layout="wide")
st.title("Overview")
st.caption("Normalised performance of all live portfolios")

# ---------------------------------------------------------------------------
# Live portfolio snapshots
# ---------------------------------------------------------------------------


def _load_all_snapshots() -> dict[str, pd.DataFrame]:
    """Return {strategy_id: DataFrame with date index and portfolio_value column}."""
    result: dict[str, pd.DataFrame] = {}

    if not _PORTFOLIOS_DIR.exists():
        return result

    for portfolio_dir in sorted(_PORTFOLIOS_DIR.iterdir()):
        if not portfolio_dir.is_dir():
            continue
        snapshots_file = portfolio_dir / "snapshots.json"
        if not snapshots_file.exists():
            continue

        try:
            records = json.loads(snapshots_file.read_text())
            if not records:
                continue
            df = pd.DataFrame(records)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            result[portfolio_dir.name] = df
        except Exception:
            continue

    return result


snapshots = _load_all_snapshots()

if snapshots:
    st.subheader("Portfolio performance (normalised to % return)")

    fig = go.Figure()

    for strategy_id, df in snapshots.items():
        if "portfolio_value" not in df.columns or df.empty:
            continue
        base = df["portfolio_value"].iloc[0]
        if base == 0:
            continue
        pct_return = (df["portfolio_value"] / base - 1) * 100

        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=pct_return,
                mode="lines",
                name=strategy_id,
                hovertemplate="%{y:.2f}%<extra>" + strategy_id + "</extra>",
            )
        )

    fig.update_layout(
        template="plotly_dark",
        yaxis_title="Return (%)",
        xaxis_title="Date",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=500,
    )

    st.plotly_chart(fig, use_container_width=True)

else:
    st.info(
        "No live portfolio snapshots found. "
        "Run the daily session script to populate data/portfolios/."
    )

st.divider()

# ---------------------------------------------------------------------------
# Backtest results
# ---------------------------------------------------------------------------

st.subheader("Backtest results")


def _load_factor_research() -> pd.DataFrame | None:
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


backtest_df = _load_factor_research()

if backtest_df is not None and not backtest_df.empty:
    # Format numeric columns as percentages where applicable.
    display_df = backtest_df.copy()
    pct_cols = [
        c
        for c in display_df.columns
        if "return" in c.lower() or "drawdown" in c.lower()
    ]
    for col in pct_cols:
        if pd.api.types.is_numeric_dtype(display_df[col]):
            display_df[col] = display_df[col].apply(
                lambda v: f"{v:.2%}" if pd.notna(v) else ""
            )

    st.dataframe(display_df, use_container_width=True)
else:
    st.info(
        "No backtest results found. "
        "Run: python scripts/run_all_combos.py to generate data/factor-research.json"
    )

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.divider()
st.markdown(
    "Made with care by [William](https://william.revah.paris)",
    unsafe_allow_html=True,
)
