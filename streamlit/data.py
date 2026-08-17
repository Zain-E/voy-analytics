"""
Data-access layer for the Voy retention dashboard.

All reads target the dbt MARTS (not the raw tables) — the dashboard is a pure
presentation layer over governed models. Configure the project / dataset in
`.streamlit/secrets.toml` (see secrets.example.toml).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from google.cloud import bigquery

# ------------------------------------------------------------------ config ----
PROJECT = st.secrets.get("gcp_project", "zains-gcp")
MARTS = st.secrets.get("marts_dataset", "voy_analytics")
SNAPSHOT_DATE = st.secrets.get("snapshot_date", "2024-08-16")

FQ = lambda t: f"`{PROJECT}.{MARTS}.{t}`"  # noqa: E731


@st.cache_resource(show_spinner=False)
def _client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT)


@st.cache_data(ttl=3600, show_spinner="Querying BigQuery…")
def run(sql: str) -> pd.DataFrame:
    return _client().query(sql).result().to_dataframe()


def _dim_filter(country: str, taxonomy: str) -> str:
    clauses = []
    if country and country != "All":
        clauses.append(f"country = '{country}'")
    if taxonomy and taxonomy != "All":
        clauses.append(f"acq_taxonomy = '{taxonomy}'")
    return (" and " + " and ".join(clauses)) if clauses else ""


# ------------------------------------------------------------------ filters ---
@st.cache_data(ttl=3600, show_spinner=False)
def filter_options() -> tuple[list[str], list[str]]:
    df = run(f"""
        select distinct country, acq_taxonomy
        from {FQ('dim_customer')}
        where is_ever_active
    """)
    countries = ["All"] + sorted(df["country"].dropna().unique().tolist())
    taxonomies = ["All"] + sorted(df["acq_taxonomy"].dropna().unique().tolist())
    return countries, taxonomies


# --------------------------------------------------------------------- KPIs ---
def kpis(country: str, taxonomy: str) -> dict:
    f = _dim_filter(country, taxonomy)
    reg = run(f"""
        select
          count(*)                                as registered,
          countif(is_ever_active)                 as ever_active
        from {FQ('dim_customer')}
        where true {f}
    """).iloc[0]

    # Latest 32-day-window active, at the last available day (flagged as soft tail).
    aw = run(f"""
        with d as (
          select day, sum(active_window) as active_window
          from {FQ('rpt_active_users_daily')}
          where true {f}
          group by day
        )
        select active_window from d order by day desc limit 1
    """)
    active_window = int(aw["active_window"].iloc[0]) if len(aw) else 0

    # Latest COMPLETE-month MAU (exclude the partial snapshot month).
    mau = run(f"""
        with m as (
          select month, count(distinct if(is_active, customer_id, null)) as mau
          from {FQ('fct_customer_month')}
          where month < date_trunc(date '{SNAPSHOT_DATE}', month) {f}
          group by month
        )
        select mau from m order by month desc limit 1
    """)
    latest_mau = int(mau["mau"].iloc[0]) if len(mau) else 0

    registered = int(reg["registered"])
    ever_active = int(reg["ever_active"])
    return {
        "registered": registered,
        "ever_active": ever_active,
        "activation_rate": ever_active / registered if registered else 0,
        "latest_mau": latest_mau,
        "active_window": active_window,
    }


# ------------------------------------------------------------ active users ----
def active_users_monthly(country: str, taxonomy: str) -> pd.DataFrame:
    f = _dim_filter(country, taxonomy)
    return run(f"""
        select
          month,
          count(distinct if(is_active, customer_id, null))          as mau,
          count(distinct if(is_active_survival, customer_id, null)) as mau_survival
        from {FQ('fct_customer_month')}
        where month < date_trunc(date '{SNAPSHOT_DATE}', month) {f}
        group by month
        order by month
    """)


def active_users_daily(country: str, taxonomy: str) -> pd.DataFrame:
    f = _dim_filter(country, taxonomy)
    return run(f"""
        select
          day,
          sum(dau)           as dau,
          sum(active_window) as active_window
        from {FQ('rpt_active_users_daily')}
        where true {f}
        group by day
        order by day
    """)


# --------------------------------------------------------------- retention ----
def cohort_retention_pivot(metric: str, country: str, taxonomy: str,
                           max_tenure: int = 24) -> pd.DataFrame:
    """metric in {'survival','activity'} -> cohort_month x tenure matrix of %."""
    f = _dim_filter(country, taxonomy)
    num = "retained_survival" if metric == "survival" else "retained_activity"
    df = run(f"""
        select
          cohort_month,
          tenure,
          safe_divide(sum({num}), sum(cohort_size)) as retention
        from {FQ('rpt_cohort_retention')}
        where tenure <= {max_tenure} {f}
        group by cohort_month, tenure
    """)
    if df.empty:
        return df
    pivot = df.pivot(index="cohort_month", columns="tenure", values="retention")
    return pivot.sort_index()


def retention_curve(country: str, taxonomy: str, max_tenure: int = 24) -> pd.DataFrame:
    """Weighted survival + activity retention by tenure across all cohorts."""
    f = _dim_filter(country, taxonomy)
    return run(f"""
        select
          tenure,
          safe_divide(sum(retained_survival), sum(cohort_size)) as survival,
          safe_divide(sum(retained_activity), sum(cohort_size)) as activity
        from {FQ('rpt_cohort_retention')}
        where tenure <= {max_tenure} {f}
        group by tenure
        order by tenure
    """)


# ------------------------------------------------------------------ churn -----
def monthly_churn(country: str, taxonomy: str) -> pd.DataFrame:
    f = _dim_filter(country, taxonomy)
    df = run(f"""
        with m as (
          select month, customer_id
          from {FQ('fct_customer_month')}
          where is_active {f}
        )
        select
          date_add(p.month, interval 1 month)      as month,
          count(distinct p.customer_id)            as active_prev,
          count(distinct c.customer_id)            as retained
        from m p
        left join m c
          on c.customer_id = p.customer_id
         and c.month = date_add(p.month, interval 1 month)
        group by 1
        order by 1
    """)
    if df.empty:
        return df
    df["churn_rate"] = 1 - (df["retained"] / df["active_prev"])
    # Exclude the partial snapshot month from the churn view.
    cutoff = pd.Timestamp(SNAPSHOT_DATE).to_period("M").to_timestamp()
    df = df[pd.to_datetime(df["month"]) < cutoff]
    return df


# ------------------------------------------------------------ acquisition -----
def acquisition(country: str, taxonomy: str) -> pd.DataFrame:
    f = _dim_filter(country, taxonomy)
    return run(f"""
        select cohort_month as month, count(*) as new_customers
        from {FQ('dim_customer')}
        where is_ever_active
          and cohort_month < date_trunc(date '{SNAPSHOT_DATE}', month) {f}
        group by cohort_month
        order by cohort_month
    """)
