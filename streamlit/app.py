"""
Voy — Customer Retention Dashboard (Streamlit)

A stakeholder-facing view over the dbt marts: active users, cohort retention
(survival + activity), churn, and acquisition — drillable by country and
acquisition taxonomy.

Run:  streamlit run streamlit/app.py
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import data as d

# --------------------------------------------------------------- palette ------
# Validated categorical hues (dataviz reference palette). Assigned in fixed order.
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, MUTED, GRID = "#0b0b0b", "#898781", "#e1e0d9"
CRITICAL = "#d03b3b"
# Sequential blue ramp (light -> dark) for the heatmap.
BLUE_RAMP = [
    [0.0, "#f4f8fe"], [0.2, "#cde2fb"], [0.4, "#86b6ef"],
    [0.6, "#3987e5"], [0.8, "#1c5cab"], [1.0, "#0d366b"],
]

st.set_page_config(page_title="Voy · Retention", page_icon="📈", layout="wide")

BASE_LAYOUT = dict(
    template="simple_white",
    font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=INK, size=13),
    margin=dict(l=10, r=10, t=40, b=10),
    hovermode="x unified",
    plot_bgcolor="#fcfcfb",
    paper_bgcolor="#fcfcfb",
    colorway=[BLUE, ORANGE, AQUA, YELLOW],
)


def _style(fig: go.Figure, title: str = "") -> go.Figure:
    fig.update_layout(**BASE_LAYOUT, title=dict(text=title, font=dict(size=16)))
    fig.update_xaxes(showgrid=False, color=MUTED, linecolor="#c3c2b7")
    fig.update_yaxes(showgrid=True, gridcolor=GRID, color=MUTED, zeroline=False)
    return fig


# ------------------------------------------------------------------ header ----
st.title("Voy · Customer Retention")
st.caption(
    "Customer-level retention, churn and active users over the subscription base. "
    f"Data as of **{d.SNAPSHOT_DATE}**. The partial final month is excluded from all trends."
)

# ------------------------------------------------------------------ filters ---
countries, taxonomies = d.filter_options()
with st.sidebar:
    st.header("Filters")
    country = st.selectbox("Country", countries, index=0)
    taxonomy = st.selectbox("Acquisition group", taxonomies, index=0)
    st.markdown("---")
    st.caption(
        "Active user = holds a live subscription **and** was active in the last "
        "32 days. Retention is **survival** (never churned) with an **activity** "
        "overlay (win-backs included)."
    )

# --------------------------------------------------------------------- KPIs ---
k = d.kpis(country, taxonomy)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Registered customers", f"{k['registered']:,}")
c2.metric("Ever-active", f"{k['ever_active']:,}")
c3.metric("Activation rate", f"{k['activation_rate']:.1%}")
c4.metric("Latest MAU (complete month)", f"{k['latest_mau']:,}")
c5.metric("Active · 32-day (latest day)", f"{k['active_window']:,}", help="Soft at the extract tail — see notes.")

st.markdown("---")

# --------------------------------------------------------- active users -------
st.subheader("Active users over time")
au = d.active_users_monthly(country, taxonomy)
if not au.empty:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=au["month"], y=au["mau"], name="Monthly active (MAU)",
                             mode="lines", line=dict(color=BLUE, width=2)))
    fig.add_trace(go.Scatter(x=au["month"], y=au["mau_survival"], name="Still in first subscription (survival)",
                             mode="lines", line=dict(color=ORANGE, width=2, dash="dot")))
    st.plotly_chart(_style(fig, "Monthly active customers"), use_container_width=True)

with st.expander("Daily active detail (DAU vs 32-day window)"):
    ad = d.active_users_daily(country, taxonomy)
    if not ad.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=ad["day"], y=ad["active_window"], name="Active · last 32 days",
                                 mode="lines", line=dict(color=BLUE, width=2)))
        fig.add_trace(go.Scatter(x=ad["day"], y=ad["dau"], name="Live on day (DAU)",
                                 mode="lines", line=dict(color=AQUA, width=1.5)))
        st.plotly_chart(_style(fig, "Daily active customers"), use_container_width=True)

st.markdown("---")

# ------------------------------------------------------------- retention ------
st.subheader("Cohort retention")
left, right = st.columns([3, 2])

with left:
    metric = st.radio("Metric", ["survival", "activity"], horizontal=True,
                      format_func=lambda x: "Survival (never churned)" if x == "survival"
                      else "Activity (win-backs included)")
    pivot = d.cohort_retention_pivot(metric, country, taxonomy, max_tenure=24)
    if not pivot.empty:
        z = pivot.values * 100
        fig = go.Figure(go.Heatmap(
            z=z,
            x=[f"M{t}" for t in pivot.columns],
            y=[pd.Timestamp(m).strftime("%Y-%m") for m in pivot.index],
            colorscale=BLUE_RAMP, zmin=0, zmax=100,
            colorbar=dict(title="%", ticksuffix="%"),
            hovertemplate="Cohort %{y} · %{x}<br>%{z:.1f}%<extra></extra>",
        ))
        fig.update_layout(**{**BASE_LAYOUT, "hovermode": "closest"},
                          title=dict(text=f"{metric.title()} retention by cohort", font=dict(size=16)))
        fig.update_yaxes(autorange="reversed", color=MUTED)
        fig.update_xaxes(color=MUTED, side="top")
        st.plotly_chart(fig, use_container_width=True)

with right:
    curve = d.retention_curve(country, taxonomy, max_tenure=24)
    if not curve.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=curve["tenure"], y=curve["survival"] * 100,
                                 name="Survival", mode="lines", line=dict(color=BLUE, width=2)))
        fig.add_trace(go.Scatter(x=curve["tenure"], y=curve["activity"] * 100,
                                 name="Activity", mode="lines", line=dict(color=ORANGE, width=2)))
        fig.update_yaxes(ticksuffix="%")
        fig.update_xaxes(title="Months since acquisition")
        st.plotly_chart(_style(fig, "Retention curve (all cohorts, weighted)"), use_container_width=True)
        st.caption("Gap between the lines = reactivation.")

st.markdown("---")

# ----------------------------------------------------------------- churn ------
cc1, cc2 = st.columns(2)
with cc1:
    st.subheader("Monthly churn")
    ch = d.monthly_churn(country, taxonomy)
    if not ch.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=ch["month"], y=ch["churn_rate"] * 100, name="Churn rate",
                                 mode="lines", line=dict(color=CRITICAL, width=2)))
        fig.update_yaxes(ticksuffix="%")
        st.plotly_chart(_style(fig, "Month-over-month logo churn"), use_container_width=True)

with cc2:
    st.subheader("Acquisition")
    acq = d.acquisition(country, taxonomy)
    if not acq.empty:
        fig = go.Figure(go.Bar(x=acq["month"], y=acq["new_customers"],
                               marker_color=BLUE, name="New customers"))
        st.plotly_chart(_style(fig, "New customers by acquisition month"), use_container_width=True)

# ----------------------------------------------------------------- notes ------
with st.expander("Definitions & data-quality notes"):
    st.markdown(
        """
- **Active user** — holds a subscription covering the day **and** active within the
  last **32 days** (`active_window_days`). With interval-only data the two conditions
  coincide; the 32-day window is the robust headline.
- **Survival retention** (primary) — share of a cohort still inside their *first
  unbroken* subscription at tenure *n*. Monotonically decreasing.
- **Activity retention** (secondary) — share *active* at tenure *n*, win-backs
  included. Always ≥ survival; the gap is reactivation.
- **Churn** — logo (customer-count) churn. Revenue churn (NRR/GRR) needs price/plan
  data, which isn't in this dataset.
- **Tail** — the extract only holds closed spells, so "live on the final day" is
  under-captured (~9.6k vs ~195k on the 32-day window). The partial final month is
  excluded from every trend above.
        """
    )
