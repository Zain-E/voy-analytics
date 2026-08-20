# Streamlit dashboard

Presentation layer over the dbt marts. No business logic lives here — every
number comes from `viz_*` / `fct_*` / `dim_*`.

Three tabs:

| Tab | What's on it | Source |
|---|---|---|
| Overview | Headline KPIs, this-month scorecards, active users, acquisition | `data.py` → marts |
| Retention | Cohort retention heatmap, retention curves, monthly churn | `data.py` → marts |
| Data model | Architecture DAG + ERD, zoomable and pannable | `data_model.py` → the dbt project files |

The Data model tab is generated from the dbt project rather than drawn by hand:
`models/**/*.sql` gives the lineage (every arrow is a real `ref()`), `models/**/*.yml`
gives grains, columns and tests, and `target/catalog.json` — when the project has been
built — gives real BigQuery column types.

Neither diagram is laid out in the browser — Mermaid can't lay one out in a tab that's
hidden on first paint, and the dashboard shouldn't need a CDN. The lineage DAG is Mermaid
pre-rendered to `assets/lineage.svg`; regenerate it after changing a model:

```bash
python streamlit/data_model.py     # rewrites assets/lineage.mmd + .svg (needs node)
```

The tab warns on itself if that render no longer matches the project, so a missed
regeneration shows up rather than passing silently. The ERD is drawn straight to SVG in
Python on every render (`erd_svg()`), so it needs no regeneration at all; its star layout
— `dim_customer` in the middle — is declared, because every arrow points away from the hub
and an auto-layout engine would push it to one edge. Zoom and pan are inline JS in both.

## Run

```bash
cd streamlit
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# auth as a read-only principal
gcloud auth application-default login

cp secrets.example.toml .streamlit/secrets.toml   # then edit project/dataset
streamlit run app.py
```

## Prerequisite

The dbt models must be built first so the marts exist:

```bash
dbt build   # from the repo root, with a configured profile
```

Point `marts_dataset` in secrets at the dataset dbt wrote to (default `voy_analytics`).
