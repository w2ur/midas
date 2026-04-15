"""Strategy deep-dive page — single strategy performance, positions, trades, metrics."""

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
_STRATEGIES_DIR = _ROOT / "data" / "strategies"

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Strategy — Midas", page_icon="👑", layout="wide")
st.title("Strategy deep dive")

# ---------------------------------------------------------------------------
# Available portfolios
# ---------------------------------------------------------------------------


def _available_portfolios() -> list[str]:
    if not _PORTFOLIOS_DIR.exists():
        return []
    return sorted(
        d.name
        for d in _PORTFOLIOS_DIR.iterdir()
        if d.is_dir() and (d / "portfolio.json").exists()
    )


portfolios = _available_portfolios()

if not portfolios:
    st.info(
        "No active portfolios found. "
        "Run the daily session script to populate data/portfolios/."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Strategy selectbox
# ---------------------------------------------------------------------------

strategy_id = st.selectbox("Select strategy", portfolios)
portfolio_dir = _PORTFOLIOS_DIR / strategy_id

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> list | dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


snapshots_raw = _load_json(portfolio_dir / "snapshots.json")
portfolio_raw = _load_json(portfolio_dir / "portfolio.json")
trades_raw = _load_json(portfolio_dir / "trades.json")

# ---------------------------------------------------------------------------
# Performance chart
# ---------------------------------------------------------------------------

st.subheader("Performance")

if snapshots_raw:
    df_snap = pd.DataFrame(snapshots_raw)
    df_snap["date"] = pd.to_datetime(df_snap["date"])
    df_snap = df_snap.set_index("date").sort_index()

    if "portfolio_value" in df_snap.columns and not df_snap.empty:
        base = df_snap["portfolio_value"].iloc[0]
        pct = (df_snap["portfolio_value"] / base - 1) * 100 if base else df_snap["portfolio_value"]

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=df_snap.index,
                y=pct,
                mode="lines",
                name=strategy_id,
                fill="tozeroy",
                hovertemplate="%{y:.2f}%<extra></extra>",
            )
        )
        fig.update_layout(
            template="plotly_dark",
            yaxis_title="Return (%)",
            xaxis_title="Date",
            height=400,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No portfolio_value data in snapshots.")
else:
    st.info("No snapshots found for this strategy.")

st.divider()

# ---------------------------------------------------------------------------
# Current positions
# ---------------------------------------------------------------------------

st.subheader("Current positions")

if portfolio_raw and portfolio_raw.get("positions"):
    positions_df = pd.DataFrame(portfolio_raw["positions"])
    st.dataframe(positions_df, use_container_width=True)
    st.caption(f"Cash: **${portfolio_raw.get('cash', 0):,.2f}**")
else:
    st.info("No open positions.")

st.divider()

# ---------------------------------------------------------------------------
# Trade history
# ---------------------------------------------------------------------------

st.subheader("Trade history")

if trades_raw:
    df_trades = pd.DataFrame(trades_raw)
    if not df_trades.empty:
        # Ensure consistent column order with reasoning last.
        priority_cols = ["timestamp", "action", "ticker", "shares", "price", "total", "reasoning"]
        other_cols = [c for c in df_trades.columns if c not in priority_cols]
        ordered_cols = [c for c in priority_cols if c in df_trades.columns] + other_cols
        df_trades = df_trades[ordered_cols]

        st.dataframe(df_trades, use_container_width=True)

        # Key metrics
        st.subheader("Metrics")
        total = len(df_trades)
        buys = len(df_trades[df_trades["action"] == "BUY"]) if "action" in df_trades.columns else 0
        sells = len(df_trades[df_trades["action"] == "SELL"]) if "action" in df_trades.columns else 0

        m1, m2, m3 = st.columns(3)
        m1.metric("Total trades", total)
        m2.metric("BUY", buys)
        m3.metric("SELL", sells)
    else:
        st.info("Trade log is empty.")
else:
    st.info("No trade history found.")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.divider()
st.markdown(
    "Made with care by [William](https://william.revah.paris)",
    unsafe_allow_html=True,
)
