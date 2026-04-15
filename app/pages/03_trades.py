"""Trade log page — full trade history across all strategies with filters."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
_PORTFOLIOS_DIR = _ROOT / "data" / "portfolios"

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Trades — Midas", page_icon="👑", layout="wide")
st.title("Trade log")
st.caption("All trades across all strategies")

# ---------------------------------------------------------------------------
# Load all trades
# ---------------------------------------------------------------------------


def _load_all_trades() -> pd.DataFrame:
    """Merge trades.json from every portfolio into one DataFrame."""
    frames: list[pd.DataFrame] = []

    if not _PORTFOLIOS_DIR.exists():
        return pd.DataFrame()

    for portfolio_dir in sorted(_PORTFOLIOS_DIR.iterdir()):
        if not portfolio_dir.is_dir():
            continue
        trades_file = portfolio_dir / "trades.json"
        if not trades_file.exists():
            continue

        try:
            records = json.loads(trades_file.read_text())
            if not records:
                continue
            df = pd.DataFrame(records)
            df["strategy_id"] = portfolio_dir.name
            frames.append(df)
        except Exception:
            continue

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    if "timestamp" in combined.columns:
        combined["timestamp"] = pd.to_datetime(combined["timestamp"])
        combined = combined.sort_values("timestamp", ascending=False)
    return combined


df_all = _load_all_trades()

if df_all.empty:
    st.info(
        "No trades found. "
        "Trades are recorded when a strategy executes a buy or sell."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

col1, col2, col3 = st.columns(3)

with col1:
    strategies = ["ALL"] + sorted(df_all["strategy_id"].unique().tolist())
    selected_strategy = st.selectbox("Strategy", strategies)

with col2:
    if "timestamp" in df_all.columns and not df_all["timestamp"].isna().all():
        min_date = df_all["timestamp"].min().date()
        max_date = df_all["timestamp"].max().date()
        date_range = st.date_input(
            "Date range",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )
    else:
        date_range = None

with col3:
    action_options = ["ALL", "BUY", "SELL"]
    selected_action = st.selectbox("Action", action_options)

# ---------------------------------------------------------------------------
# Apply filters
# ---------------------------------------------------------------------------

filtered = df_all.copy()

if selected_strategy != "ALL":
    filtered = filtered[filtered["strategy_id"] == selected_strategy]

if date_range and len(date_range) == 2 and "timestamp" in filtered.columns:
    start_dt = pd.Timestamp(date_range[0])
    end_dt = pd.Timestamp(date_range[1]) + pd.Timedelta(days=1)
    filtered = filtered[(filtered["timestamp"] >= start_dt) & (filtered["timestamp"] < end_dt)]

if selected_action != "ALL" and "action" in filtered.columns:
    filtered = filtered[filtered["action"] == selected_action]

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

st.caption(f"{len(filtered)} trade(s) shown")

# Column order: timestamp, strategy, action, ticker, shares, price, reasoning
priority_cols = ["timestamp", "strategy_id", "action", "ticker", "shares", "price", "total", "reasoning"]
other_cols = [c for c in filtered.columns if c not in priority_cols]
ordered_cols = [c for c in priority_cols if c in filtered.columns] + other_cols
filtered = filtered[ordered_cols]

st.dataframe(filtered, use_container_width=True)

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.divider()
st.markdown(
    "Made with care by [William](https://william.revah.paris)",
    unsafe_allow_html=True,
)
