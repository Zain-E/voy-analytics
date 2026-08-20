"""
Data-access layer for the Voy retention dashboard.

All reads target the dbt MARTS (not the raw tables) — the dashboard is a pure
presentation layer over governed models. Configure the project / dataset — and,
when deploying, the service-account key — in `.streamlit/secrets.toml`
(see secrets.example.toml).

Performance model
-----------------
The dashboard is *network-bound*, not CPU-bound: the marts return small monthly
aggregates, so the cost is the BigQuery round-trip, not local dataframe work.
Three layers keep it fast:

1. **On-disk parquet cache** (polars). The reporting snapshot is immutable
   (`SNAPSHOT_DATE`), so every query result is a pure function of its SQL. We
   hash the SQL, keep the result as parquet under `.voy_cache/<snapshot>/`, and
   never re-query it — so a cold *process* start after the first ever run pays
   zero BigQuery latency. Bust the cache by rebuilding dbt to a new snapshot.
2. **In-memory memoisation** (`st.cache_data`). Streamlit re-runs the whole
   script on every interaction; this keeps each result frame hot in RAM so
   re-runs don't even touch disk.
3. **Concurrent cold-load** (`prefetch`). The first paint needs ~9 independent
   queries; `prefetch()` fires them through a thread pool so they overlap
   instead of running back-to-back.

Downloads use the **BigQuery Storage API** (Arrow) and one merged scan feeds all
the monthly metrics, so the big fact table is read once, not three times.
"""
from __future__ import annotations

import concurrent.futures as cf
import functools
import hashlib
import os
from pathlib import Path

import pandas as pd
import polars as pl
import streamlit as st
from google.cloud import bigquery
from google.oauth2 import service_account


def _cfg(key: str, default: str) -> str:
    """Config from st.secrets → env var (KEY.upper()) → default.

    st.secrets raises StreamlitSecretNotFoundError if NO secrets file exists at
    all — even when a default is passed — so guard it and fall back gracefully.
    """
    try:
        return st.secrets.get(key, os.environ.get(key.upper(), default))
    except Exception:
        return os.environ.get(key.upper(), default)


# ------------------------------------------------------------------ config ----
PROJECT = _cfg("gcp_project", "zains-gcp")
MARTS = _cfg("marts_dataset", "voy_analytics")
SOURCE = _cfg("source_dataset", "voy")   # raw source dataset (all-time subscriptions count)
SNAPSHOT_DATE = _cfg("snapshot_date", "2024-08-16")

FQ = lambda t: f"`{PROJECT}.{MARTS}.{t}`"  # noqa: E731

# Immutable-snapshot result cache. Keyed by snapshot so a dbt rebuild to a new
# snapshot naturally lands in a fresh directory.
_CACHE_DIR = Path(os.environ.get("VOY_CACHE_DIR", ".voy_cache")) / SNAPSHOT_DATE
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _service_account_credentials():
    """Credentials from a [gcp_service_account] secrets block, or None.

    Local dev uses Application Default Credentials (`gcloud auth application-default
    login`), but a hosted runtime like Streamlit Community Cloud has no gcloud and no
    metadata server, so there the key is supplied as a secret instead. None means
    "fall back to ADC", which is what bigquery.Client does with credentials=None.
    """
    try:
        info = st.secrets.get("gcp_service_account")
    except Exception:                       # no secrets file at all — local ADC
        return None
    if not info:
        return None
    return service_account.Credentials.from_service_account_info(
        dict(info), scopes=["https://www.googleapis.com/auth/cloud-platform"])


# Resolved once at import, on the main thread, so the worker threads in prefetch()
# never reach into st.secrets themselves.
_CREDENTIALS = _service_account_credentials()


def _visible_secret_keys() -> list[str]:
    """Top-level secret NAMES only — never values. Used to explain a missing key."""
    try:
        return sorted(st.secrets.keys())
    except Exception:
        return []


@functools.lru_cache(maxsize=1)
def _bq_client() -> bigquery.Client:
    """Plain (non-Streamlit) singleton so it's safe to use from worker threads."""
    try:
        return bigquery.Client(project=PROJECT, credentials=_CREDENTIALS)
    except Exception as exc:
        if _CREDENTIALS is not None:
            raise
        # credentials=None means google-auth walked its chain to the GCE metadata
        # server and timed out there — a TransportError that never mentions the
        # actual cause. Off GCE that only ever means "nobody configured a key".
        raise RuntimeError(
            "No BigQuery credentials. Add a [gcp_service_account] section to this "
            "app's secrets (Manage app -> Settings -> Secrets), pasted WITHOUT the "
            "leading '# ' comment markers — see streamlit/secrets.example.toml. "
            f"Secrets the app can currently see: {_visible_secret_keys() or 'none'}. "
            "Locally, `gcloud auth application-default login` works instead."
        ) from exc


def _cache_path(sql: str) -> Path:
    return _CACHE_DIR / f"{hashlib.sha256(sql.encode()).hexdigest()[:16]}.parquet"


def _fetch(sql: str) -> pd.DataFrame:
    """Disk-cached fetch: parquet on disk → else BigQuery (Arrow) → parquet.

    Thread-safe (no Streamlit calls) so `prefetch` can run it concurrently.
    Both the cached and fresh paths return via `pd.read_parquet`, so dtypes are
    identical regardless of which path served the frame.
    """
    p = _cache_path(sql)
    if p.exists():
        return pd.read_parquet(p)
    arrow = _bq_client().query(sql).result().to_arrow(create_bqstorage_client=True)
    pl.from_arrow(arrow).write_parquet(p)          # polars: fast, compressed write
    return pd.read_parquet(p)


@st.cache_data(show_spinner=False)
def run(sql: str) -> pd.DataFrame:
    """Main-thread entry: in-memory memoised over the disk-cached fetch."""
    return _fetch(sql)


def _dim_filter(country: str, taxonomy: str) -> str:
    clauses = []
    if country and country != "All":
        clauses.append(f"country = '{country}'")
    if taxonomy and taxonomy != "All":
        clauses.append(f"acq_taxonomy = '{taxonomy}'")
    return (" and " + " and ".join(clauses)) if clauses else ""


# ============================================================ SQL builders ====
# One builder per distinct query. Shared by the public functions AND by
# `prefetch`, so the concurrently-warmed SQL is byte-identical to what the
# functions later request (no cache-key drift).

def _sql_filter_options() -> str:
    return f"""
        select distinct country, acq_taxonomy
        from {FQ('dim_customer')}
        where is_ever_active
    """


def _sql_reg(f: str) -> str:
    return f"""
        select
          count(*)                 as registered,
          countif(is_ever_active)  as ever_active
        from {FQ('dim_customer')}
        where true {f}
    """


def _sql_active_window(f: str) -> str:
    return f"""
        with d as (
          select day, sum(active_customers_32d) as active_window
          from {FQ('viz_active_users_daily')}
          where true {f}
          group by day
        )
        select active_window from d order by day desc limit 1
    """


def _sql_monthly(f: str) -> str:
    """One scan of the fact table feeding every monthly metric (MAU, survival,
    new, churned, reactivated) — replaces three separate scans."""
    return f"""
        select
          calendar_month as month,
          count(distinct if(has_active_subscription, customer_id, null))                  as mau,
          count(distinct if(has_continuous_active_subscription, customer_id, null))       as mau_survival,
          count(distinct if(is_new_customer, customer_id, null))                          as new_customers,
          count(distinct if(has_churned_this_month, customer_id, null))                   as churned,
          count(distinct if(has_reactivated_subscription_this_month, customer_id, null))  as reactivated
        from {FQ('fct_customer_per_month_snapshot')}
        where calendar_month < date_trunc(date '{SNAPSHOT_DATE}', month) {f}
        group by calendar_month
        order by calendar_month
    """


def _sql_total_subs(f: str) -> str:
    return f"""
        select count(distinct a.subscription_id) as n
        from `{PROJECT}.{SOURCE}.activity` a
        join {FQ('dim_customer')} d using (customer_id)
        where true {f}
    """


def _sql_acquisition(f: str) -> str:
    return f"""
        select cohort_month as month, count(*) as new_customers
        from {FQ('dim_customer')}
        where is_ever_active
          and cohort_month < date_trunc(date '{SNAPSHOT_DATE}', month) {f}
        group by cohort_month
        order by cohort_month
    """


def _sql_monthly_churn(f: str) -> str:
    return f"""
        with m as (
          select calendar_month, customer_id
          from {FQ('fct_customer_per_month_snapshot')}
          where has_active_subscription {f}
        )
        select
          date_add(p.calendar_month, interval 1 month)   as month,
          count(distinct p.customer_id)                  as active_prev,
          count(distinct c.customer_id)                  as retained
        from m p
        left join m c
          on c.customer_id = p.customer_id
         and c.calendar_month = date_add(p.calendar_month, interval 1 month)
        group by 1
        order by 1
    """


def _sql_cohort(metric: str, f: str, max_tenure: int) -> str:
    num = "retained_never_churned" if metric == "never_churned" else "retained_total"
    return f"""
        select
          cohort_month,
          tenure,
          safe_divide(sum({num}), sum(cohort_size)) as retention
        from {FQ('viz_cohort_retention')}
        where tenure <= {max_tenure} {f}
        group by cohort_month, tenure
    """


def _sql_retention_curve(f: str, max_tenure: int) -> str:
    """All cohorts pooled: weighted never-churned + total retention by tenure."""
    return f"""
        select
          tenure,
          safe_divide(sum(retained_never_churned), sum(cohort_size)) as never_churned,
          safe_divide(sum(retained_total), sum(cohort_size)) as total
        from {FQ('viz_cohort_retention')}
        where tenure <= {max_tenure} {f}
        group by tenure
        order by tenure
    """


# ------- acquisition-group breakdowns (respect the sidebar country/group filter) --
def _sql_group_registered(f: str) -> str:
    return f"""
        select acq_taxonomy, count(*) as registered
        from {FQ('dim_customer')}
        where true {f}
        group by acq_taxonomy
    """


def _sql_group_subs(f: str) -> str:
    return f"""
        select d.acq_taxonomy, count(distinct a.subscription_id) as subscriptions
        from `{PROJECT}.{SOURCE}.activity` a
        join {FQ('dim_customer')} d using (customer_id)
        where true {f}
        group by d.acq_taxonomy
    """


def _sql_group_mau(f: str) -> str:
    """Latest complete-month MAU per acquisition group."""
    return f"""
        with m as (
          select acq_taxonomy, calendar_month,
            count(distinct if(has_active_subscription, customer_id, null)) as mau
          from {FQ('fct_customer_per_month_snapshot')}
          where calendar_month < date_trunc(date '{SNAPSHOT_DATE}', month) {f}
          group by acq_taxonomy, calendar_month
        )
        select acq_taxonomy, mau from m
        where calendar_month = (select max(calendar_month) from m)
    """


def _sql_retention_group_curve(f: str, max_tenure: int) -> str:
    """Weighted never-churned + total retention by tenure, per acquisition group —
    one retention curve per group (M0…M{max_tenure})."""
    return f"""
        select
          acq_taxonomy,
          tenure,
          safe_divide(sum(retained_never_churned), sum(cohort_size)) as never_churned,
          safe_divide(sum(retained_total), sum(cohort_size)) as total
        from {FQ('viz_cohort_retention')}
        where tenure <= {max_tenure} {f}
        group by acq_taxonomy, tenure
        order by acq_taxonomy, tenure
    """


# --------------------------------------------------------------- prefetch -----
def prefetch(country: str, taxonomy: str, max_tenure: int = 12) -> None:
    """Warm the cache for the whole dashboard concurrently (cold-load fan-out).

    Only queries not already on disk are fetched, so this is a no-op once warm.
    Runs the raw thread-safe `_fetch` (never `st.*`) inside a thread pool.
    """
    f = _dim_filter(country, taxonomy)
    sqls = [
        _sql_filter_options(),
        _sql_reg(f),
        _sql_active_window(f),
        _sql_monthly(f),
        _sql_total_subs(f),
        _sql_acquisition(f),
        _sql_monthly_churn(f),
        _sql_cohort("never_churned", f, max_tenure),
        _sql_cohort("total", f, max_tenure),
        _sql_retention_curve(f, max_tenure),
        _sql_group_registered(f),
        _sql_group_subs(f),
        _sql_group_mau(f),
        _sql_retention_group_curve(f, max_tenure),
    ]
    missing = [s for s in sqls if not _cache_path(s).exists()]
    if not missing:
        return
    with cf.ThreadPoolExecutor(max_workers=min(8, len(missing))) as ex:
        list(ex.map(_fetch, missing))


# ------------------------------------------------------------------ filters ---
@st.cache_data(show_spinner=False)
def filter_options() -> tuple[list[str], list[str]]:
    df = run(_sql_filter_options())
    countries = ["All"] + sorted(df["country"].dropna().unique().tolist())
    taxonomies = ["All"] + sorted(df["acq_taxonomy"].dropna().unique().tolist())
    return countries, taxonomies


# ------------------------------------------------------------ monthly base ----
def monthly_base(country: str, taxonomy: str) -> pd.DataFrame:
    """Single cached scan of the fact table — the source for MAU, survival, new,
    churned and reactivated. `monthly_metrics` / `active_users_monthly` slice it."""
    return run(_sql_monthly(_dim_filter(country, taxonomy)))


# --------------------------------------------------------------------- KPIs ---
def kpis(country: str, taxonomy: str) -> dict:
    f = _dim_filter(country, taxonomy)
    reg = run(_sql_reg(f)).iloc[0]

    aw = run(_sql_active_window(f))
    active_window = int(aw["active_window"].iloc[0]) if len(aw) else 0

    mb = monthly_base(country, taxonomy)          # reuses the shared cached scan
    latest_mau = int(mb["mau"].iloc[-1]) if len(mb) else 0

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
    return monthly_base(country, taxonomy)[["month", "mau", "mau_survival"]]


def total_subscriptions(country: str, taxonomy: str) -> int:
    """Total distinct subscriptions ever created (a 'scale of the book' stat).

    The one place the app reads the SOURCE table rather than a mart — no mart
    carries the all-time distinct subscription count. Joined to dim_customer so
    the country / taxonomy filters still apply.
    """
    df = run(_sql_total_subs(_dim_filter(country, taxonomy)))
    return int(df["n"].iloc[0]) if len(df) else 0


def monthly_metrics(country: str, taxonomy: str) -> pd.DataFrame:
    """Per-month customer counts (complete months only): MAU, new, churned, reactivated."""
    return monthly_base(country, taxonomy)[
        ["month", "mau", "new_customers", "churned", "reactivated"]
    ]


def active_users_daily(country: str, taxonomy: str) -> pd.DataFrame:
    f = _dim_filter(country, taxonomy)
    return run(f"""
        select
          day,
          sum(daily_active_customers) as dau,
          sum(active_customers_32d)   as active_window,
          sum(live_subscriptions)     as live_subscriptions
        from {FQ('viz_active_users_daily')}
        where true {f}
        group by day
        order by day
    """)


# --------------------------------------------------------------- retention ----
def cohort_retention_pivot(metric: str, country: str, taxonomy: str,
                           max_tenure: int = 24) -> pd.DataFrame:
    """metric in {'never_churned','total'} -> cohort_month x tenure matrix of %."""
    f = _dim_filter(country, taxonomy)
    df = run(_sql_cohort(metric, f, max_tenure))
    if df.empty:
        return df
    pivot = df.pivot(index="cohort_month", columns="tenure", values="retention")
    return pivot.sort_index()


def retention_curve(country: str, taxonomy: str, max_tenure: int = 24) -> pd.DataFrame:
    """Weighted never-churned + total retention by tenure across all cohorts."""
    return run(_sql_retention_curve(_dim_filter(country, taxonomy), max_tenure))


# ------------------------------------------------------------------ churn -----
def monthly_churn(country: str, taxonomy: str) -> pd.DataFrame:
    f = _dim_filter(country, taxonomy)
    df = run(_sql_monthly_churn(f))
    if df.empty:
        return df
    df = df.copy()
    df["churn_rate"] = 1 - (df["retained"] / df["active_prev"])
    # Exclude the partial snapshot month from the churn view.
    cutoff = pd.Timestamp(SNAPSHOT_DATE).to_period("M").to_timestamp()
    df = df[pd.to_datetime(df["month"]) < cutoff]
    return df


# ------------------------------------------------------------ acquisition -----
def acquisition(country: str, taxonomy: str) -> pd.DataFrame:
    return run(_sql_acquisition(_dim_filter(country, taxonomy)))


# ------------------------------------------------ acquisition-group breakdowns --
def overview_by_group(country: str, taxonomy: str) -> pd.DataFrame:
    """Per acquisition group: registered customers, total subscriptions, latest MAU.
    Respects the sidebar country/group filter (collapses to one row if a group is set)."""
    f = _dim_filter(country, taxonomy)
    reg = run(_sql_group_registered(f))
    if reg.empty:
        return reg
    subs = run(_sql_group_subs(f))
    mau = run(_sql_group_mau(f))
    out = (reg.merge(subs, on="acq_taxonomy", how="left")
              .merge(mau, on="acq_taxonomy", how="left"))
    out[["subscriptions", "mau"]] = out[["subscriptions", "mau"]].fillna(0).astype(int)
    return out.sort_values("registered", ascending=False).reset_index(drop=True)


def retention_by_group_curve(country: str, taxonomy: str, max_tenure: int = 12) -> pd.DataFrame:
    """Retention curve (by tenure) for each acquisition group — long format:
    acq_taxonomy, tenure, never_churned, total."""
    return run(_sql_retention_group_curve(_dim_filter(country, taxonomy), max_tenure))
