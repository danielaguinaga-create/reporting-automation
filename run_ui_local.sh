#!/bin/bash
cd "$(dirname "$0")" || exit 1
exec .venv/bin/streamlit run src/reporting_automation/ui_app.py --server.port 8501 --server.headless true
