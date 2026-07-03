"""Midas dashboard — entry point."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Page config — must be first Streamlit call
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Midas — AI Fund Manager",
    page_icon="👑",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[1]
_STRATEGIES_DIR = _ROOT / "data" / "strategies"
_PORTFOLIOS_DIR = _ROOT / "data" / "portfolios"
_FACTOR_RESEARCH = _ROOT / "data" / "factor-research.json"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _count_strategy_specs() -> int:
    if not _STRATEGIES_DIR.exists():
        return 0
    return len(list(_STRATEGIES_DIR.glob("*.json")))


def _count_active_portfolios() -> int:
    if not _PORTFOLIOS_DIR.exists():
        return 0
    return sum(
        1
        for d in _PORTFOLIOS_DIR.iterdir()
        if d.is_dir() and (d / "portfolio.json").exists()
    )


def _count_backtested_combos() -> int:
    if not _FACTOR_RESEARCH.exists():
        return 0
    import json

    try:
        data = json.loads(_FACTOR_RESEARCH.read_text())
        # New shape: {"generated_at", "git_sha", "args", "results": [...]}.
        # Legacy shape: a bare list of result rows.
        rows = data.get("results", []) if isinstance(data, dict) else data
        if isinstance(rows, list):
            return len(rows)
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("👑 Midas")
st.caption("AI Fund Manager — Everything I touch turns to JSON")

st.divider()

# ---------------------------------------------------------------------------
# Key metrics
# ---------------------------------------------------------------------------

col1, col2, col3 = st.columns(3)

with col1:
    specs = _count_strategy_specs()
    st.metric("Strategy Specs", specs, help="JSON files in data/strategies/")

with col2:
    portfolios = _count_active_portfolios()
    st.metric(
        "Active Portfolios", portfolios, help="Portfolios with portfolio.json on disk"
    )

with col3:
    combos = _count_backtested_combos()
    st.metric("Backtested Combos", combos, help="Rows in data/factor-research.json")

st.divider()

# ---------------------------------------------------------------------------
# Quick navigation hints
# ---------------------------------------------------------------------------

st.subheader("Navigate")
st.markdown(
    """
| Page | Description |
|------|-------------|
| **Overview** | Normalised performance chart for all live portfolios + backtest summary |
| **Strategy** | Deep-dive into a single strategy: positions, trades, metrics |
| **Trades** | Full trade log across all strategies with filters |
| **Backtest** | Factor-research results as sortable table and heatmap |
| **Leaderboard** | Strategy rankings by return, Sharpe, or max drawdown |
"""
)

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.divider()
st.markdown(
    "Made with care by [William](https://william.revah.paris)",
    unsafe_allow_html=True,
)
