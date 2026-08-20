# Voy — Customer Retention Models

A dbt + BigQuery model of Voy's subscription retention, with a Streamlit dashboard
for stakeholders. Built for the Lead Data Engineer exercise.

The project rests on one decision: a customer counts as active whether they hold one
subscription or five. Each customer's subscription spells are merged into continuous
periods, and retention, churn and active users are all measured from those periods.

## Live dashboard

**[Open the dashboard on Streamlit Community Cloud →](https://voy-analytics-zain-test.streamlit.app/)**

Click that link to see the finished work without installing anything. It has three
tabs: **Overview** and **Retention** for the metrics, and **Data model** for the dbt
architecture DAG, ERD and table summaries. The setup steps further down are only
needed if you want to run it locally.

## Architecture

dbt generates the model docs and lineage, but also exists in the 'data model' tab.

```bash
./run_docs.sh          # dbt deps + docs generate + docs serve → http://localhost:8080
```


Where to start reading the models:

- `int_customer_continuous_subscriptions` merges each customer's spells into
  continuous periods. Everything downstream is built on it.
- `fct_customer_per_month_snapshot` is the table to query: one row per customer per
  month, drillable, not pre-aggregated.
- `int_subscription_active_periods` runs the same merge per subscription. That is what
  makes the gaps between spells measurable (`dim_subscription.active_days` and
  `gap_days`) and gives the daily model something gap-aware to count, since a
  subscription's first and last dates alone would hide the lapses in between.
- The `viz_*` models are reporting rollups for the dashboard.

Definitions, join keys and caveats for the marts are in
[`models/marts/context.md`](models/marts/context.md). Metric formulas and SQL are in
[`docs/metric_definitions.md`](docs/metric_definitions.md). Revenue retention (NRR/GRR)
is out of scope: the source data has no price or plan.

## Run it

```bash
gcloud auth application-default login   # one-time — authenticate as yourself

# dbt: activate a venv with dbt-bigquery, then build
python3 -m venv .venv && source .venv/bin/activate && pip install dbt-bigquery
./run_dbt.sh          # dbt deps + dbt build (models + tests)

# dashboard (installs into your active venv)
./run_streamlit.sh
```

`./run_dbt.sh` passes extra arguments straight to dbt, e.g. `./run_dbt.sh test`. It
points dbt at `dbt_profiles/profiles.yml` via `--profiles-dir`; that profile has a
`dev` target using local oauth and a `prod` target using a service account.
`./run_streamlit.sh` reads `streamlit/.streamlit/secrets.toml`, which you copy from
`streamlit/secrets.example.toml`.

<details><summary>Manual steps (if you'd rather not use the scripts)</summary>

```bash
# 1. dbt models  (needs a profile — see profiles.example.yml; read-only source access)
dbt deps
dbt build            # runs models + tests

# 2. dashboard
cd streamlit
pip install -r requirements.txt
cp secrets.example.toml .streamlit/secrets.toml   # edit project/dataset
streamlit run app.py
```
</details>
