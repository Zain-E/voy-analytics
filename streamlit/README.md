# Streamlit dashboard

Presentation layer over the dbt marts. No business logic lives here — every
number comes from `viz_*` / `fct_*` / `dim_*`.

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
