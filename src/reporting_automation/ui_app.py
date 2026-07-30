from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("DYLD_LIBRARY_PATH", "/opt/homebrew/lib")

import streamlit as st
from google.cloud import bigquery

from reporting_automation.config.client_registry import ClientRegistry
from reporting_automation.config.loader import load_settings
from reporting_automation.config.models import ReportKind
from reporting_automation.config.registry import ReportRegistry
from reporting_automation.orchestrator import run_report
from reporting_automation.query.bigquery_client import BigQueryExecutor

st.set_page_config(page_title="Reporting Automation", page_icon="📊")


@st.cache_resource
def _load_registries():
    settings = load_settings()
    registry = ReportRegistry()
    registry.load(settings.reports_dir)
    client_registry = ClientRegistry()
    client_registry.load(settings.clients_dir)
    return settings, registry, client_registry


settings, registry, client_registry = _load_registries()

st.title("Reporting Automation")
st.caption(
    "Corre un reporte contra BigQuery y descarga el resultado. "
    "No manda correos ni sube a Drive -- eso sigue siendo solo desde la CLI (`run --deliver`)."
)

report_ids = sorted(r.id for r in registry.list_all())
if not report_ids:
    st.warning("No hay reportes registrados.")
    st.stop()

report_id = st.selectbox("Reporte", report_ids)
report = registry.get(report_id)
if report.description:
    st.caption(report.description)

if report.kind == ReportKind.CUSTOM and report.client_id:
    client_id = report.client_id
    st.text_input("Cliente", value=client_id, disabled=True)
else:
    known_clients = sorted(c.id for c in client_registry.list_all())
    if not known_clients:
        st.warning("No hay clientes registrados.")
        st.stop()
    client_id = st.selectbox("Cliente", known_clients)

client_config = client_registry.get_or_none(client_id)
client_params = client_config.bq_params if client_config else {}

params: dict[str, str] = {}
params_needing_input = {
    name: bq_type for name, bq_type in report.params_schema.items() if name not in client_params
}

if client_params:
    resolved_preview = ", ".join(f"{k}={v}" for k, v in client_params.items())
    st.caption(f"Resuelto automáticamente desde el cliente: {resolved_preview}")

if params_needing_input:
    st.subheader("Parámetros")
    st.caption("Déjalos vacíos para usar el valor por defecto del reporte (si tiene uno).")
    for name, bq_type in params_needing_input.items():
        default_hint = report.params_defaults.get(name, "")
        value = st.text_input(
            f"{name} ({bq_type})",
            value="",
            placeholder=f"default: {default_hint}" if default_hint else "requerido",
            key=f"param_{name}",
        )
        if value:
            params[name] = value

if st.button("Ejecutar reporte", type="primary"):
    with st.spinner(f"Corriendo {report_id!r} para {client_id!r} contra BigQuery..."):
        executor = BigQueryExecutor(bigquery.Client(project=settings.gcp_project))
        output_dir = Path("/tmp/reporting_automation_ui") / client_id
        result = run_report(
            report_id=report_id,
            client_id=client_id,
            params=params,
            output_dir=output_dir,
            registry=registry,
            executor=executor,
            client_registry=client_registry,
        )

    if result.status == "failure":
        st.error(f"Error ejecutando {report_id!r}: {result.error}")
    else:
        st.success(f"OK: {result.rows} filas x {result.columns} columnas")
        for rendered in result.rendered_files:
            st.download_button(
                label=f"Descargar {rendered.filename}",
                data=rendered.local_path.read_bytes(),
                file_name=rendered.filename,
                key=f"download_{rendered.filename}",
            )
