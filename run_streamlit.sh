#!/usr/bin/env bash
#
# Launch the Voy retention dashboard.
#
#   ./run_streamlit.sh
#
# Assumes a virtualenv is already ACTIVE — activate it first, e.g.:
#   python3 -m venv .venv && source .venv/bin/activate
# Installs the dashboard requirements into the active environment, then launches.
# Reads config from streamlit/.streamlit/secrets.toml (copy from secrets.example.toml).
# Auth uses Application Default Credentials:  gcloud auth application-default login
#
set -euo pipefail
cd "$(dirname "$0")/streamlit"

if [ -z "${VIRTUAL_ENV:-}" ]; then
  echo "⚠  No virtualenv active — installing into the current Python environment." >&2
  echo "   Activate your venv first if that's not what you want:  source .venv/bin/activate" >&2
fi

echo "› Installing requirements into the active environment…"
python -m pip install -q -r requirements.txt

if [ ! -f ".streamlit/secrets.toml" ]; then
  echo "⚠  No streamlit/.streamlit/secrets.toml found."
  echo "   mkdir -p .streamlit && cp secrets.example.toml .streamlit/secrets.toml  (then edit)"
fi

exec python -m streamlit run app.py
