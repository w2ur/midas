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

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Streamlit runs pages with ``app/pages`` on ``sys.path``, not the repo root,
# so the ``engine`` package is not importable unless we add the root ourselves.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.config import get_config
from engine.manager_report import (
    authored_status,
    build_manager_summary,
    index_manager_inbox,
    load_decisions,
    load_resolved,
    read_portfolio,
    read_snapshots,
)

# ---------------------------------------------------------------------------
# Paths + constants
# ---------------------------------------------------------------------------

_PORTFOLIOS_DIR = _ROOT / "data" / "portfolios"

_cfg = get_config()
if not _cfg.allocators:
    st.info(
        "No allocator configured — this page requires an agent with role=allocator."
    )
    st.stop()

_MANAGER_ID = _cfg.allocators[0]
_ALLOCATOR_SPEC = _cfg.allocator_spec(_MANAGER_ID)
_ORDERS_DIR = _ROOT / "data" / "orders"
_REVIEW_DIR = _ORDERS_DIR / f"{_ALLOCATOR_SPEC.channels_prefix}-review"
_INBOX_DIR = _ORDERS_DIR / f"{_ALLOCATOR_SPEC.channels_prefix}-inbox"
_PENDING_DIR = _ORDERS_DIR / f"{_ALLOCATOR_SPEC.channels_prefix}-pending"

_BASELINE_ID = "baseline-manager"
_INITIAL_CAPITAL_EUR = 2000.0
_MIN_CONVICTION = _ALLOCATOR_SPEC.risk_budget.min_conviction

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

_TRIGGER_OP_SYMBOLS = {">=": "≥", "<=": "≤"}


def _position_label(position: dict, status: str) -> str:
    """`BUY TICKER €N` — annotated with the conditional trigger and the order's
    terminal status so an armed/expired conditional never reads as a fill."""
    label = (
        f"{position.get('action', '?').upper()} {position.get('ticker', '?')} "
        f"€{position.get('size_eur', 0):,.0f}"
    )
    trigger = position.get("trigger")
    if trigger:
        op = _TRIGGER_OP_SYMBOLS.get(trigger.get("op", ""), trigger.get("op", ""))
        label += f" · {op}{trigger.get('level', 0):g}"
    if status:
        label += f" — {status}"
    return label


if decisions:
    inbox_index = index_manager_inbox(_INBOX_DIR)
    rows = []
    for d in decisions:
        positions_d = d.get("positions", []) or []
        if positions_d:
            statuses = authored_status(
                d,
                agent_id=_MANAGER_ID,
                inbox_index=inbox_index,
                pending_dir=_PENDING_DIR,
            )
            summary_txt = "; ".join(
                _position_label(p, status) for p, status in zip(positions_d, statuses)
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
