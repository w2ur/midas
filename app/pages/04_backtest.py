"""Backtest results page — sortable table and optional heatmap from factor-research.json."""

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
_FACTOR_RESEARCH = _ROOT / "data" / "factor-research.json"

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Backtest — Midas", page_icon="👑", layout="wide")
st.title("Backtest results")
st.caption("Factor research: selector × manager × universe combinations")

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------


def _load_results() -> pd.DataFrame | None:
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


df = _load_results()

if df is None or df.empty:
    st.info(
        "No backtest results found. "
        "Run: python scripts/run_all_combos.py to generate data/factor-research.json"
    )
    st.stop()

# ---------------------------------------------------------------------------
# Sortable results table
# ---------------------------------------------------------------------------

st.subheader("Results table")

sort_col = st.selectbox(
    "Sort by",
    options=[
        c
        for c in ["total_return", "sharpe", "max_drawdown", "num_trades"]
        if c in df.columns
    ],
    index=0,
)
ascending = st.checkbox("Ascending", value=False)
sorted_df = (
    df.sort_values(sort_col, ascending=ascending) if sort_col in df.columns else df
)

# Format numeric columns for display.
display_df = sorted_df.copy()
for col in ["total_return", "max_drawdown"]:
    if col in display_df.columns and pd.api.types.is_numeric_dtype(display_df[col]):
        display_df[col] = display_df[col].apply(
            lambda v: f"{v:.2%}" if pd.notna(v) else ""
        )
if "sharpe" in display_df.columns and pd.api.types.is_numeric_dtype(
    display_df["sharpe"]
):
    display_df["sharpe"] = display_df["sharpe"].apply(
        lambda v: f"{v:.3f}" if pd.notna(v) else ""
    )

st.dataframe(display_df, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Heatmap: selectors × managers → total_return
# ---------------------------------------------------------------------------

has_heatmap_cols = all(
    c in df.columns for c in ["universe", "selector", "manager", "total_return"]
)

if has_heatmap_cols:
    st.subheader("Heatmap: total return by selector × manager")

    universes = sorted(df["universe"].dropna().unique().tolist())
    selected_universe = st.selectbox("Universe", universes)

    subset = df[df["universe"] == selected_universe]

    if subset.empty:
        st.info("No data for this universe.")
    else:
        pivot = subset.pivot_table(
            index="selector",
            columns="manager",
            values="total_return",
            aggfunc="mean",
        )

        selectors = pivot.index.tolist()
        managers = pivot.columns.tolist()
        z_values = pivot.values.tolist()

        # Format annotations as percentages.
        text_values = [
            [f"{v:.1%}" if pd.notna(v) else "" for v in row] for row in pivot.values
        ]

        fig = go.Figure(
            go.Heatmap(
                z=z_values,
                x=managers,
                y=selectors,
                text=text_values,
                texttemplate="%{text}",
                colorscale="RdYlGn",
                colorbar=dict(title="Total Return"),
                hovertemplate="Selector: %{y}<br>Manager: %{x}<br>Return: %{text}<extra></extra>",
            )
        )
        fig.update_layout(
            template="plotly_dark",
            xaxis_title="Manager",
            yaxis_title="Selector",
            height=max(300, len(selectors) * 50 + 100),
        )
        st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.divider()
st.markdown(
    "Made with care by [William](https://william.revah.paris)",
    unsafe_allow_html=True,
)
