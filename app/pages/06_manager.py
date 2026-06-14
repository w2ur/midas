"""Manager page (PRIVATE) — how the real-money decision agent is performing.

The Manager (`the-manager`) is the real-money decision path: it consumes
structured research notes from the analysts, authors orders on a separate
channel, fills into a private paper book, and is graded against a deterministic
twin (`baseline-manager`) toward a Gate C go/no-go decision (~mid-August).

This view is deliberately NOT on the public site (midas.revah.paris) — it lives
on the local Streamlit dashboard only, matching the "private to user initially"
stance on the real-money path. It renders cleanly before the Manager has ever
run (all panels show an explicit empty state).

All reads / NAV maths live in engine.manager_report (stdlib-only, unit-tested);
this file is a thin pandas/plotly view over those helpers.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.manager_report import (
    build_manager_summary,
    load_decisions,
    load_resolved,
    read_portfolio,
    read_snapshots,
)

# ---------------------------------------------------------------------------
# Paths + constants
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
_PORTFOLIOS_DIR = _ROOT / "data" / "portfolios"
_REVIEW_DIR = _ROOT / "data" / "orders" / "manager-review"

_MANAGER_ID = "the-manager"
_BASELINE_ID = "baseline-manager"
_INITIAL_CAPITAL_EUR = 2000.0
_MIN_CONVICTION = 7  # mirrors RISK_BUDGET_LIMITS["min_conviction"]

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Manager — Midas", page_icon="👑", layout="wide")
st.title("The Manager")
st.caption(
    "Private view of the real-money decision agent — graded against its "
    "deterministic baseline twin toward a Gate C decision (~mid-August). "
    "Not shown on the public site."
)

# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

manager_snaps = read_snapshots(_PORTFOLIOS_DIR / _MANAGER_ID / "snapshots.json")
baseline_snaps = read_snapshots(_PORTFOLIOS_DIR / _BASELINE_ID / "snapshots.json")
manager_portfolio = read_portfolio(_PORTFOLIOS_DIR / _MANAGER_ID / "portfolio.json")
decisions = load_decisions(_REVIEW_DIR)
resolved = load_resolved(_REVIEW_DIR / "resolved.json")

summary = build_manager_summary(manager_snaps, baseline_snaps, _INITIAL_CAPITAL_EUR)

if not summary["has_run"] and not decisions:
    st.info(
        "The Manager has not run yet — first weekday session pending. "
        "Once it runs, this page shows its NAV vs the baseline, its decisions, "
        "and resolved 10-day outcomes."
    )
    st.stop()


# ---------------------------------------------------------------------------
# 1. Status strip
# ---------------------------------------------------------------------------


def _fmt_eur(v: float | None) -> str:
    return f"€{v:,.0f}" if v is not None else "—"


def _fmt_pct(v: float | None) -> str:
    return f"{v:+.2f}%" if v is not None else "—"


c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "Manager NAV",
    _fmt_eur(summary["manager_nav"]),
    _fmt_pct(summary["manager_return_pct"]),
    help=f"Since €{_INITIAL_CAPITAL_EUR:,.0f} inception.",
)
c2.metric(
    "Baseline NAV",
    _fmt_eur(summary["baseline_nav"]),
    _fmt_pct(summary["baseline_return_pct"]),
)
c3.metric(
    "Gap vs baseline",
    _fmt_pct(summary["gap_pct"]),
    help="Manager return minus baseline-manager return — the Gate C signal.",
)
cash = manager_portfolio.get("cash") if manager_portfolio else None
n_positions = len(manager_portfolio.get("positions", [])) if manager_portfolio else 0
c4.metric("Cash / positions", f"{_fmt_eur(cash)} / {n_positions}")

st.divider()

# ---------------------------------------------------------------------------
# 2. NAV over time — Manager vs baseline
# ---------------------------------------------------------------------------

st.subheader("NAV over time")

if manager_snaps or baseline_snaps:
    fig = go.Figure()
    for snaps, name, color in (
        (manager_snaps, "the-manager", "#c9a227"),
        (baseline_snaps, "baseline-manager", "#6b7280"),
    ):
        if not snaps:
            continue
        df = pd.DataFrame(snaps)
        df["date"] = pd.to_datetime(df["date"])
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["portfolio_value"],
                mode="lines",
                name=name,
                line=dict(
                    color=color, dash="solid" if name == "the-manager" else "dot"
                ),
                hovertemplate="€%{y:,.0f}<extra>" + name + "</extra>",
            )
        )
    fig.add_hline(
        y=_INITIAL_CAPITAL_EUR,
        line=dict(color="#9ca3af", width=1, dash="dash"),
        annotation_text="inception",
        annotation_position="bottom right",
    )
    fig.update_layout(
        template="plotly_dark",
        yaxis_title="NAV (€)",
        xaxis_title="Date",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=420,
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No NAV snapshots yet.")

st.divider()

# ---------------------------------------------------------------------------
# 3. Current positions
# ---------------------------------------------------------------------------

st.subheader("Current positions")

positions = manager_portfolio.get("positions", []) if manager_portfolio else []
if positions:
    st.dataframe(pd.DataFrame(positions), use_container_width=True, hide_index=True)
else:
    st.caption("Flat — no open positions (the Manager holds cash below conviction ≥7).")

st.divider()

# ---------------------------------------------------------------------------
# 4. Decision log
# ---------------------------------------------------------------------------

st.subheader("Decision log")
st.caption(
    f"One audit artifact per weekday. Conviction ≥{_MIN_CONVICTION} is required to "
    "place orders — early HOLD decisions are expected, not a malfunction."
)

if decisions:
    rows = []
    for d in decisions:
        positions_d = d.get("positions", []) or []
        if positions_d:
            summary_txt = "; ".join(
                f"{p.get('action', '?').upper()} {p.get('ticker', '?')} "
                f"€{p.get('size_eur', 0):,.0f}"
                for p in positions_d
            )
        else:
            summary_txt = "HOLD — " + (d.get("hold_reasoning", "") or "")
        rows.append(
            {
                "date": d.get("date", ""),
                "conviction": d.get("conviction"),
                "decision": summary_txt,
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
else:
    st.caption("No decisions recorded yet.")

st.divider()

# ---------------------------------------------------------------------------
# 5. Resolved outcomes
# ---------------------------------------------------------------------------

st.subheader("Resolved outcomes")
st.caption(
    "Each non-HOLD position scored 10 trading days after the decision — realised "
    "return and alpha versus MSCI World. This is the actual performance signal."
)

if resolved:
    df = pd.DataFrame(resolved)
    preferred = ["date", "ticker", "action", "realized_return_pct", "alpha_vs_msci_pct"]
    cols = [c for c in preferred if c in df.columns] + [
        c for c in df.columns if c not in preferred
    ]
    df = df[cols].sort_values("date", ascending=False)
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.caption(
        "No matured outcomes yet — decisions resolve ~10 trading days after they "
        "are made."
    )

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.divider()
st.markdown(
    "Made with care by [William](https://william.revah.paris)",
    unsafe_allow_html=True,
)
