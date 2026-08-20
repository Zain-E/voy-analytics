"""
Voy — Customer Retention Dashboard (Streamlit)

Three tabs:
  • Overview   — Voy overview KPIs, this-month scorecards (MoM deltas + sparklines),
                 active users, acquisition
  • Retention  — cohort retention heatmap (2022+), monthly churn
  • Data model — architecture DAG, ERD and table summaries, generated from the dbt
                 project itself (see data_model.py)

Themed to Voy's brand (forest green / olive / peach on cream · Manrope, Poppins wordmark).
Pure presentation layer over the dbt marts (see data.py).

Run:  streamlit run streamlit/app.py
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import data as d
import data_model as dm

# ----------------------------------------------------------------- Voy brand --
GREEN        = "#0F3D2E"   # primary forest green (headings, values, primary series)
GREEN_MID    = "#4E7C59"
OLIVE        = "#9BA84B"   # yellow-green accent
LIME         = "#C7D24F"
PEACH        = "#E3B08A"
TERRA        = "#C4703E"   # warm terracotta (churn / caution)
INK          = "#09110A"
MUTED        = "#6B6B63"
SURFACE      = "#FBFAF6"   # warm cream panels
BG           = "#FFFFFF"
GRID         = "#E8E7DE"
POS          = "#1E8E5A"   # delta up (good)
NEG          = "#C0392B"   # delta down (bad)
FONT         = "Manrope, system-ui, -apple-system, sans-serif"

# qualitative cycle for multi-series charts (one colour per acquisition group)
CYCLE = ["#0F3D2E", "#9BA84B", "#C4703E", "#4E7C59", "#E3B08A", "#C7D24F", "#6B6B63", "#09110A"]

# green sequential ramp for the heatmap (light cream -> deep forest green)
VOY_RAMP = [
    [0.00, "#f5f7ee"], [0.20, "#dfe6c2"], [0.40, "#b9c98a"],
    [0.60, "#7d9a54"], [0.80, "#3f6b45"], [1.00, "#0f3d2e"],
]

st.set_page_config(page_title="Voy · Retention", page_icon="📈", layout="wide")

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=Poppins:wght@700;800&display=swap');
    html, body, .stApp, [class*="css"], [data-testid="stAppViewContainer"] {
        font-family: 'Manrope', system-ui, sans-serif;
    }
    .stApp { background-color: #FFFFFF; }
    h1, h2, h3, h4 { font-family: 'Manrope', sans-serif; color: #0F3D2E; font-weight: 800; letter-spacing: -0.01em; }
    [data-testid="stMetric"] { background: #FBFAF6; border: 1px solid #E8E7DE; border-radius: 16px; padding: 16px 18px; }
    [data-testid="stMetricValue"] { color: #0F3D2E; font-weight: 800; }
    [data-testid="stMetricLabel"] { color: #6B6B63; font-weight: 600; }
    button[data-baseweb="tab"] { font-weight: 700; font-size: 1.02rem; }
    [data-baseweb="tab-highlight"] { background-color: #0F3D2E !important; }
    button[data-baseweb="tab"][aria-selected="true"] { color: #0F3D2E !important; }
    section[data-testid="stSidebar"] { background: #FBFAF6; }
    /* Metric (toggle) — segmented control: selected button in Voy green */
    [data-testid="stSegmentedControl"] button[aria-checked="true"],
    [data-testid="stSegmentedControl"] button[aria-selected="true"] {
        background-color: #0F3D2E !important; color: #FFFFFF !important; border-color: #0F3D2E !important;
    }
    [data-testid="stSegmentedControl"] button { font-weight: 600; }
    </style>
    """,
    unsafe_allow_html=True,
)

BASE_LAYOUT = dict(
    template="simple_white",
    font=dict(family=FONT, color=INK, size=13),
    margin=dict(l=10, r=10, t=36, b=10),
    hovermode="x unified",
    plot_bgcolor=BG, paper_bgcolor=BG,
    colorway=[GREEN, OLIVE, PEACH, GREEN_MID],
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                bgcolor="rgba(0,0,0,0)"),
)


def _style(fig: go.Figure) -> go.Figure:
    fig.update_layout(**BASE_LAYOUT)
    fig.update_xaxes(showgrid=False, color=MUTED, linecolor=GRID)
    fig.update_yaxes(showgrid=False, color=MUTED, zeroline=False, tickformat=",")
    return fig


def _rgba(hex_color: str, alpha: float) -> str:
    """Hex '#RRGGBB' -> 'rgba(r,g,b,alpha)' for area fills."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _hlabel(v) -> str:
    """Compact human label: 3K, 170K, 1.5M (thousands rounded to the nearest K)."""
    v = float(v)
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f}M".replace(".0M", "M")
    if abs(v) >= 1_000:
        return f"{round(v / 1_000):,}K"
    return f"{int(round(v))}"


def _delta_pct(cur: float, prev: float):
    if prev and prev != 0:
        return (cur - prev) / prev * 100
    return None


def _spark_svg(vals, color, h: int = 34) -> str:
    vals = [float(v) for v in vals if v is not None and not pd.isna(v)]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n, W = len(vals), 100.0
    pts = [(i / (n - 1) * W, h - ((v - lo) / rng) * (h - 6) - 3) for i, v in enumerate(vals)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    lx, ly = pts[-1]
    return (f'<svg width="100%" height="{h}" viewBox="0 0 {W:.0f} {h}" preserveAspectRatio="none" '
            f'style="display:block;overflow:visible;">'
            f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="1.6" '
            f'vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="1.7" fill="{color}" vector-effect="non-scaling-stroke"/></svg>')


def _card(col, label, value, delta_pct, series, color, good_up=True, help_text=""):
    """A monthly KPI card (single cream panel): label + ? tooltip, big value,
    MoM delta, and a 12-month sparkline — all inside one bordered card."""
    if delta_pct is None:
        delta_html = '<div style="height:20px;"></div>'
    else:
        up = delta_pct >= 0
        good = up if good_up else (not up)
        c = POS if good else NEG
        arrow = "↑" if up else "↓"
        delta_html = (f'<div style="color:{c};font-weight:700;font-size:0.9rem;margin-top:4px;">'
                      f'{arrow} {delta_pct:+.1f}% MoM</div>')
    if help_text:
        info = (f'<span title="{help_text}" style="display:inline-flex;align-items:center;'
                f'justify-content:center;width:16px;height:16px;margin-left:6px;border-radius:50%;'
                f'border:1.5px solid #B7B7AE;color:#8A8A80;font-size:0.7rem;font-weight:700;'
                f'cursor:help;line-height:1;vertical-align:middle;font-family:Manrope,sans-serif;">?</span>')
    else:
        info = ""
    col.markdown(
        f'<div style="background:{SURFACE};border:1px solid {GRID};border-radius:16px;padding:16px 18px;">'
        f'<div style="color:{MUTED};font-weight:600;font-size:0.9rem;display:flex;align-items:center;">'
        f'{label}{info}</div>'
        f'<div style="color:{GREEN};font-weight:800;font-size:1.9rem;line-height:1.15;margin-top:2px;">{value}</div>'
        f'{delta_html}'
        f'<div style="margin-top:10px;">{_spark_svg(series, color)}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ================================================================== header ====
st.markdown(
    '<div style="display:flex;align-items:baseline;gap:16px;margin-bottom:0;">'
    '<span style="font-family:Poppins,Manrope,sans-serif;font-weight:800;font-size:3.2rem;'
    'color:#0F3D2E;letter-spacing:-0.05em;line-height:1;">voy</span>'
    '<span style="font-family:Manrope,sans-serif;font-weight:600;font-size:1.3rem;color:#3A3A34;">'
    'Customer Retention</span>'
    '</div>'
    '<div style="height:14px;"></div>',
    unsafe_allow_html=True,
)
st.caption(
    f"Customer-level retention, churn and active users over the subscription base. "
    f"Data as of **{d.SNAPSHOT_DATE}**. The partial final month is excluded from all trends."
)

countries, taxonomies = d.filter_options()
with st.sidebar:
    st.header("Filters")
    country = st.selectbox("Country", countries, index=0)
    taxonomy = st.selectbox("Acquisition group", taxonomies, index=0)

# Warm every query for the current filters concurrently, so the first paint
# overlaps the ~9 independent round-trips instead of running them back-to-back.
# No-op once the (immutable-snapshot) results are cached on disk.
with st.spinner("Loading dashboard…"):
    d.prefetch(country, taxonomy)

overview_tab, retention_tab, model_tab = st.tabs(["Overview", "Retention", "Data model"])

# ================================================================ OVERVIEW ====
with overview_tab:
    with st.expander("Definitions", expanded=False):
        st.markdown(
            """
- **Monthly active users (MAU)** — a user who held a **live subscription** at any point in the month.
  Independent of how many subscriptions they hold.
- **New users** — first-ever subscriptions in the month (acquisition).
- **Churned users** — users with a subscription last month, but **none** this month.
- **Reactivated users** — users with a subscription this month after being inactive last month (a win-back).
- **Activation rate** — of all registered users, the share that **ever** subscribed.
- **Active users· 32-day** — users with a live subscription across the last 32 days.  32 days is a sign that the subscription lasts at least 2 months.
- **Total subscriptions** — every subscription ever created.
            """
        )

    st.header("Voy Overview")
    k = d.kpis(country, taxonomy)
    subs = d.total_subscriptions(country, taxonomy)
    b1, b2, b3, b4 = st.columns(4)
    b1.metric("Registered users", f"{k['registered']:,}",
              help="Every user who ever registered, whether or not they subscribed.")
    b2.metric("Total subscriptions", f"{subs:,}",
              help="Every subscription ever created — one user can hold several.")
    b3.metric("Activation rate", f"{k['activation_rate']:.1%}",
              help="Of all registered users, the share that ever subscribed (ever-active ÷ registered).")
    b4.metric("Active users · 32-day", f"{k['active_window']:,}",
              help="Users with a live subscription across the last 32 days.  32 days is a sign that the subscription lasts at least 2 months.")

    st.markdown("---")

    st.subheader("This month at a glance")
    mm = d.monthly_metrics(country, taxonomy)
    if len(mm) >= 2:
        cur, prev = mm.iloc[-1], mm.iloc[-2]
        st.caption(f"Latest complete month: **{pd.Timestamp(cur['month']).strftime('%B %Y')}** "
                   f"(vs {pd.Timestamp(prev['month']).strftime('%B %Y')}) · sparklines show the last 12 months")
        tail = mm.tail(12)
        c1, c2, c3, c4 = st.columns(4)
        _card(c1, "Monthly active users (MAU)", f"{int(cur['mau']):,}",
                  _delta_pct(cur['mau'], prev['mau']), tail['mau'], GREEN, good_up=True,
                  help_text="Users who held a live subscription at any point this month, "
                            "independent of how many subscriptions they hold.")
        _card(c2, "New users", f"{int(cur['new_customers']):,}",
                  _delta_pct(cur['new_customers'], prev['new_customers']), tail['new_customers'], OLIVE, good_up=True,
                  help_text="first-ever subscriptions in the month — newly acquired users.")
        _card(c3, "Churned users", f"{int(cur['churned']):,}",
                  _delta_pct(cur['churned'], prev['churned']), tail['churned'], TERRA, good_up=False,
                  help_text="Users with a subscription last month, but none this month.")
        _card(c4, "Reactivated users", f"{int(cur['reactivated']):,}",
                  _delta_pct(cur['reactivated'], prev['reactivated']), tail['reactivated'], GREEN_MID, good_up=True,
                  help_text="Users with a subscription this month after being inactive last month — win-backs.")

    st.markdown("---")

    left, right = st.columns(2)
    with left:
        st.subheader("Active users over time")
        au = d.active_users_monthly(country, taxonomy)
        if not au.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=au["month"], y=au["mau"], name="Monthly active users (MAU)",
                                     mode="lines", line=dict(color=GREEN, width=2.5),
                                     fill="tozeroy", fillcolor=_rgba(GREEN, 0.14),
                                     hovertemplate="%{y:,.0f}<extra>Monthly active users (MAU)</extra>"))
            fig.add_trace(go.Scatter(x=au["month"], y=au["mau_survival"], name="Still in first subscription",
                                     mode="lines", line=dict(color=OLIVE, width=2, dash="dot"),
                                     fill="tozeroy", fillcolor=_rgba(OLIVE, 0.10),
                                     hovertemplate="%{y:,.0f}<extra>Still in first subscription</extra>"))
            _style(fig)
            fig.update_yaxes(visible=False)
            st.plotly_chart(fig, use_container_width=True)
    with right:
        st.subheader(
            "New users",
            help="Newly acquired users for that month (first-ever subscriptions; each customer counted once).",
        )
        acq = d.acquisition(country, taxonomy)
        if not acq.empty:
            fig = go.Figure(go.Bar(x=acq["month"], y=acq["new_customers"], marker_color=GREEN,
                                   name="Newly acquired users",
                                   hovertemplate="%{y:,.0f}<extra>Newly acquired users</extra>"))
            _style(fig)
            fig.update_yaxes(visible=False)
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    st.subheader(
        "Acquisition group",
        help="Registered users, total subscriptions and latest-month active users for each "
             "acquisition group.",
    )
    g = d.overview_by_group(country, taxonomy)
    if not g.empty:
        gmax = max(g["registered"].max(), g["subscriptions"].max(), g["mau"].max())
        fig = go.Figure()
        for col, nm, clr in (("registered", "Registered users", GREEN),
                             ("subscriptions", "Total subscriptions", OLIVE),
                             ("mau", "Monthly active (latest)", PEACH)):
            fig.add_trace(go.Bar(
                y=g["acq_taxonomy"], x=g[col], name=nm, orientation="h", marker_color=clr,
                text=[_hlabel(v) for v in g[col]], textposition="outside", textfont=dict(size=10),
                hovertemplate="%{x:,.0f}<extra>" + nm + "</extra>",
            ))
        fig.update_layout(barmode="group")
        _style(fig)
        fig.update_layout(hovermode="y unified")   # after _style, which forces x-unified
        fig.update_traces(cliponaxis=False)        # don't clip the outside labels
        fig.update_xaxes(visible=False, range=[0, gmax * 1.18])  # no x-axis; headroom for labels
        fig.update_yaxes(title="", autorange="reversed")  # biggest at top
        st.plotly_chart(fig, use_container_width=True)

# =============================================================== RETENTION ====
with retention_tab:
    with st.expander("Definitions", expanded=False):
        st.markdown(
            """
- **Cohort** — The calendar **month a customer first subscribed**. The Y-axis of the heatmap.
- **Tenure** — months since acquisition. The X-axis. M0 is the initial month of subscribing.
- **Never-churned retention** — share of a cohort still in their **first unbroken
  subscription** at tenure *n* (never churned since joining).
- **Total retention** — share of a cohort **active** at tenure *n*, **including
  win-backs** (never-churned + reactivated). Always ≥ never-churned; the gap is reactivation.  This means that a user can churn and then reactivate within the 12 month period and it will still be included.
- **Month-over-month (MoM) logo churn** — of the customers active in month *M-1*, the share who
  are **not** active in month *M*. "Logo" = customer count (not revenue). `retention = 1 − churn`.
            """
        )

    st.subheader("Cohort retention")
    _metric_label = st.segmented_control(
        "Metric", options=["Never churned", "Total (incl. win-backs)"],
        default="Never churned", selection_mode="single",
    )
    metric = "total" if _metric_label == "Total (incl. win-backs)" else "never_churned"
    pivot = d.cohort_retention_pivot(metric, country, taxonomy, max_tenure=12)
    if not pivot.empty:
        pivot = pivot.loc[[pd.Timestamp(m) >= pd.Timestamp("2022-01-01") for m in pivot.index]]

    if not pivot.empty:
        cohorts = [pd.Timestamp(m).strftime("%Y-%m") for m in pivot.index]
        tenures = [f"M{t}" for t in pivot.columns]
        z = pivot.values * 100

        _lab = "Never-churned" if metric == "never_churned" else "Total"
        st.markdown(f"**{_lab} retention** — read from left→right. Darker = higher retention. "
                    f"Cohort based retention.\n\n"
                    f"Please note, total retention includes reactivated users, which can happen at any point within the 12 month Tenure.")

        fig = go.Figure(go.Heatmap(
            z=z, x=tenures, y=cohorts,
            colorscale=VOY_RAMP, zmin=0, zmax=100, xgap=3, ygap=3,
            colorbar=dict(title="%", ticksuffix="%", outlinewidth=0),
            hovertemplate="Cohort %{y} · %{x}<br>retained %{z:.1f}%<extra></extra>",
        ))
        anns = []
        for i, coh in enumerate(cohorts):
            for j, ten in enumerate(tenures):
                v = z[i][j]
                if pd.isna(v):
                    continue
                anns.append(dict(x=ten, y=coh, text=f"{v:.0f}", showarrow=False,
                                 font=dict(family=FONT, size=10, color=("#FFFFFF" if v >= 55 else INK))))
        n = len(cohorts)
        fig.update_layout(
            annotations=anns,
            height=max(460, 30 * n + 130),
            margin=dict(l=10, r=10, t=10, b=60),
            font=dict(family=FONT, color=INK, size=12),
            plot_bgcolor=BG, paper_bgcolor=BG,
            xaxis=dict(title="Months since acquisition  (tenure →)", side="bottom",
                       color=MUTED, tickfont=dict(size=11), showgrid=False),
            yaxis=dict(title="Acquisition cohort  (month joined ↓)", autorange="reversed",
                       color=MUTED, tickfont=dict(size=11), showgrid=False),
        )
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    rleft, rright = st.columns(2)
    with rleft:
        st.subheader(
            "Retention over time",
            help="All cohorts pooled into one curve: the share of customers still retained at each "
                 "month since acquisition (M0 = joining month). Follows the metric toggle above.",
        )
        rc = d.retention_curve(country, taxonomy, max_tenure=12)
        if not rc.empty:
            _lab = "Never-churned" if metric == "never_churned" else "Total"
            st.markdown(f"**{_lab} retention**, weighted average across every cohort — how the whole base "
                        f"decays over its first 12 months. Switch the toggle above to compare.")
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=[f"M{int(t)}" for t in rc["tenure"]], y=rc[metric] * 100,
                name=f"{_lab} retention", mode="lines+markers",
                line=dict(color=GREEN, width=2.5), marker=dict(size=5, color=GREEN),
                fill="tozeroy", fillcolor=_rgba(GREEN, 0.14),
                hovertemplate="%{x} · %{y:.1f}%<extra></extra>",
            ))
            _style(fig)
            fig.update_yaxes(visible=False, rangemode="tozero")
            fig.update_xaxes(title="Months since acquisition  (tenure →)")
            st.plotly_chart(fig, use_container_width=True)

    with rright:
        st.subheader(
            "Monthly churn",
            help="Month-over-month logo churn — of customers active last month, the share not "
                 "active this month.",
        )
        ch = d.monthly_churn(country, taxonomy)
        if not ch.empty:
            ch = ch[pd.to_datetime(ch["month"]) != pd.Timestamp("2019-02-01")]  # drop skewing first month
        if not ch.empty:
            st.markdown("**Logo churn** — of customers active last month, the share **not** active "
                        "this month (customer count, not revenue).")
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=ch["month"], y=ch["churn_rate"] * 100, name="Churn rate",
                                     mode="lines", line=dict(color=TERRA, width=2.5),
                                     fill="tozeroy", fillcolor=_rgba(TERRA, 0.13),
                                     hovertemplate="%{y:.1f}%<extra>Churn rate</extra>"))
            _style(fig)
            fig.update_yaxes(ticksuffix="%", tickformat=".0f")  # percent axis, not comma
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    _lab2 = "Never-churned" if metric == "never_churned" else "Total"
    st.subheader(
        "Acquisition group retention",
        help=f"{_lab2} retention over tenure (M0…M12), one line per acquisition group — which "
             f"channel's users stick around longest. Follows the metric toggle above.",
    )
    rgc = d.retention_by_group_curve(country, taxonomy, max_tenure=12)
    if not rgc.empty:
        st.markdown(f"**{_lab2} retention by tenure**, split by acquisition group — compare how "
                    f"each channel's cohorts decay over their first 12 months. Toggle above switches metric.")
        maxT = int(rgc["tenure"].max())
        fig = go.Figure()
        for i, grp in enumerate(rgc["acq_taxonomy"].dropna().unique()):
            sub = rgc[rgc["acq_taxonomy"] == grp].sort_values("tenure")
            fig.add_trace(go.Scatter(
                x=sub["tenure"], y=sub[metric] * 100,
                name=str(grp), mode="lines+markers",
                line=dict(color=CYCLE[i % len(CYCLE)], width=2.2),
                marker=dict(size=4, color=CYCLE[i % len(CYCLE)]),
                hovertemplate=f"{grp} · M%{{x}} · %{{y:.1f}}%<extra></extra>",
            ))
        _style(fig)
        fig.update_layout(hovermode="x unified")
        fig.update_yaxes(visible=False, rangemode="tozero")
        # numeric axis anchored at 0 → line starts flush against the y-axis (no leading gap)
        fig.update_xaxes(title="Months since acquisition  (tenure →)", range=[0, maxT],
                         tickmode="array", tickvals=list(range(0, maxT + 1)),
                         ticktext=[f"M{t}" for t in range(0, maxT + 1)])
        st.plotly_chart(fig, use_container_width=True)


# =============================================================== DATA MODEL ====
with model_tab:
    st.header("Data model")
    st.caption(
        "Generated from the dbt project itself — lineage from the `ref()` / `source()` graph, "
        "For column-level docs and test results, run `./run_docs.sh`."
    )

    _s = dm.stats()
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Raw sources", _s["sources"], help="Tables in `zains-gcp.voy` — read-only inputs.")
    d2.metric("dbt models", _s["models"], help="Staging + intermediate + marts, all version-controlled SQL.")
    d3.metric("Marts", len(_s["layers"]["marts"]),
              help="The governed tables the dashboard reads — it never queries the raw sources.")
    d4.metric("Tests", _s["tests"],
              help="Declared dbt tests across every model — uniqueness, not-null, ranges, relationships.")

    st.markdown("---")

    st.subheader(
        "Architecture",
        help="The dbt DAG: how raw sources flow through staging and the gaps-and-islands merge "
             "into the marts the dashboard reads. Scroll to zoom, drag to pan.",
    )
    st.markdown(
        "**Raw sources → staging → intermediate → marts.** Each arrow is a `ref()` in the SQL, so this is the real build order."
        "`int_customer_continuous_subscriptions` merges each customer's subscription spells into continuous periods."
        "  You can consider `fct_customer_per_month_snapshot` the main table for the dashboard. "
        "The olive `viz_*` models are rollups feeding this dashboard."
    )
    dm.render_diagram("lineage", height=480)
    with st.expander("Mermaid source"):
        st.code(dm.diagram_source("lineage"), language="text")

    st.markdown("---")

    st.subheader(
        "Entity relationship diagram",
        help="Keys and cardinality across the marts. Solid lines are foreign keys; dashed lines "
             "are aggregate rollups, not key relationships. Scroll to zoom, drag to pan.",
    )
    st.markdown(
        "**Star schema**, with the hub in the middle: `dim_customer` is one row per registered "
        "user, and everything around it joins back on `customer_id`. Crow's feet mark the many "
        "side — a customer holds many subscriptions, a subscription has many activity spells and "
        "many continuous active periods, a customer has one row per month in the fact. "
        "The two gaps-and-islands merges flank the hub, and the daily model reads both: "
        "`int_customer_continuous_subscriptions` (customer grain) is what its active-customer "
        "measures count, while `int_subscription_active_periods` (subscription grain) feeds "
        "`dim_subscription`'s `active_days` / `gap_days` and the informational "
        "`live_subscriptions` count. Both exist because a start→end span silently covers the "
        "lapses between spells. "
        "**Dashed** links are aggregations (`viz_*` "
        "rollups), not foreign keys: they have no key relationship to the dimensions, only a grain "
        "of their own."
    )
    dm.render_diagram("erd", height=780)
