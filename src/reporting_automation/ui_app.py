from __future__ import annotations

import os
from datetime import date
from pathlib import Path

os.environ.setdefault("DYLD_LIBRARY_PATH", "/opt/homebrew/lib")

import streamlit as st
from google.cloud import bigquery
from pydantic import ValidationError

from reporting_automation.company_catalog import fetch_active_companies
from reporting_automation.config.client_import import slugify
from reporting_automation.config.loader import load_settings
from reporting_automation.config.models import OutputFormat, ReportKind
from reporting_automation.config.registry import ReportRegistry
from reporting_automation.exceptions import ReportConfigError
from reporting_automation.orchestrator import run_report
from reporting_automation.query.bigquery_client import BigQueryExecutor
from reporting_automation.rendering.template_registry import TemplateRegistry
from reporting_automation.report_wizard import WizardInput, save_new_report
from reporting_automation.time_window import WINDOW_PRESETS, resolve_window

_WINDOW_PARAM_NAMES = ("start_date", "end_date")
_CUSTOM_WINDOW_KEY = "custom"

st.set_page_config(page_title="Reporting Automation", page_icon="📊")


@st.cache_resource
def _load_registries():
    settings = load_settings()
    registry = ReportRegistry()
    registry.load(settings.reports_dir)
    return settings, registry


settings, registry = _load_registries()


@st.cache_data(ttl=300, show_spinner="Cargando catálogo de compañías...")
def _load_company_catalog():
    client = bigquery.Client(project=settings.gcp_project)
    return fetch_active_companies(client, settings.gcp_project, settings.bigquery_dataset)


def _company_picker(key: str) -> tuple[str, str] | None:
    """Selectbox de compania (idCompany/CompanyName) desde DimCompanies.
    Devuelve (idCompany, CompanyName), o None si el catalogo no se pudo cargar."""
    try:
        companies_df = _load_company_catalog()
    except Exception as exc:
        st.error(f"No se pudo cargar el catálogo de compañías desde BigQuery: {exc}")
        return None
    if companies_df.empty:
        st.warning("No hay compañías activas en DimCompanies.")
        return None

    company_display = dict(zip(companies_df["idCompany"], companies_df["CompanyName"]))
    id_company = st.selectbox(
        "Cliente (compañía)",
        sorted(company_display, key=lambda cid: company_display[cid]),
        format_func=lambda cid: company_display[cid],
        key=key,
    )
    return id_company, company_display[id_company]


st.title("Reporting Automation")
st.caption(
    "Corre un reporte contra BigQuery y descarga el resultado. "
    "No manda correos ni sube a Drive -- eso sigue siendo solo desde la CLI (`run --deliver`)."
)

tab_run, tab_new = st.tabs(["Correr un reporte", "Crear reporte nuevo"])

with tab_run:
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
        client_params: dict[str, str] = {}
    else:
        picked = _company_picker(key="run_client")
        if picked is None:
            st.stop()
        id_company, company_name = picked
        client_id = slugify(company_name)
        client_params = {"id_company": id_company} if "id_company" in report.params_schema else {}

    params: dict[str, str] = dict(client_params)
    params_needing_input = {
        name: bq_type for name, bq_type in report.params_schema.items() if name not in client_params
    }

    if client_params:
        resolved_preview = ", ".join(f"{k}={v}" for k, v in client_params.items())
        st.caption(f"Resuelto automáticamente desde el catálogo de compañías: {resolved_preview}")

    window_fields = [name for name in _WINDOW_PARAM_NAMES if name in params_needing_input]
    other_fields = {
        name: bq_type for name, bq_type in params_needing_input.items() if name not in window_fields
    }

    if window_fields:
        st.subheader("Ventana de tiempo")
        preset_labels = {**WINDOW_PRESETS, _CUSTOM_WINDOW_KEY: "Rango personalizado"}
        preset = st.selectbox(
            "Preset",
            list(preset_labels.keys()),
            format_func=lambda k: preset_labels[k],
            key="window_preset",
        )
        if preset == _CUSTOM_WINDOW_KEY:
            col_start, col_end = st.columns(2)
            with col_start:
                start = st.date_input("Desde", value=date.today(), key="window_start")
            with col_end:
                end = st.date_input("Hasta", value=date.today(), key="window_end")
            start_iso, end_iso = start.isoformat(), end.isoformat()
        else:
            start_iso, end_iso = resolve_window(preset, date.today())
            st.caption(f"Resuelto automáticamente: {start_iso} a {end_iso}")
        for name, value in zip(_WINDOW_PARAM_NAMES, (start_iso, end_iso)):
            if name in window_fields:
                params[name] = value

    if other_fields:
        st.subheader("Parámetros")
        st.caption("Déjalos vacíos para usar el valor por defecto del reporte (si tiene uno).")
        for name, bq_type in other_fields.items():
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

with tab_new:
    st.subheader("Crear reporte nuevo")
    st.caption(
        "Da de alta un reporte ad-hoc (YAML + SQL) sin usar la CLI. "
        "La SQL se guarda tal cual la pegues -- no se valida ni se modifica."
    )

    new_id = st.text_input("ID del reporte", key="wizard_id")
    new_name = st.text_input("Nombre", key="wizard_name")
    new_description = st.text_area("Descripción (opcional)", key="wizard_description")

    scope = st.radio(
        "Alcance",
        ["shared", "custom"],
        format_func=lambda k: (
            "Reutilizable (cualquier cliente vía id_company)"
            if k == "shared"
            else "Específico de un cliente"
        ),
        key="wizard_scope",
    )

    new_client_id = None
    if scope == "custom":
        picked = _company_picker(key="wizard_client")
        if picked is not None:
            new_client_id, _wizard_company_name = picked

    uses_time_window = st.checkbox(
        "Este reporte usa una ventana de tiempo variable (start_date/end_date)",
        key="wizard_uses_window",
    )
    param_declarations = st.text_area(
        "Variables adicionales (una por línea, formato nombre:TIPO_BQ)",
        help="Tipos válidos: STRING, INT64, FLOAT64, BOOL, DATE, DATETIME, TIMESTAMP. "
        "No hace falta declarar start_date/end_date acá si tildaste la ventana de tiempo arriba.",
        key="wizard_params",
    )
    sql_text = st.text_area(
        "SQL",
        height=200,
        help="Usa @nombre para cada variable declarada arriba (params nativos de BigQuery).",
        key="wizard_sql",
    )
    output_formats_raw = st.multiselect(
        "Formatos de salida",
        [f.value for f in OutputFormat],
        default=["csv"],
        key="wizard_output_formats",
    )

    template_registry = TemplateRegistry()
    template_registry.load(settings.templates_dir)
    template_options = ["(default)"] + template_registry.list_all()
    template_choice = st.selectbox(
        "Plantilla (opcional, para pdf/html)", template_options, key="wizard_template"
    )

    if st.button("Guardar como plantilla", type="primary"):
        try:
            if not new_id or not new_name:
                raise ValueError("ID y Nombre son obligatorios.")
            if not sql_text.strip():
                raise ValueError("La SQL no puede estar vacía.")
            if not output_formats_raw:
                raise ValueError("Elegí al menos un formato de salida.")
            if scope == "custom" and not new_client_id:
                raise ValueError("Elegí una compañía para un reporte específico de un cliente.")

            form = WizardInput(
                id=new_id,
                name=new_name,
                kind=ReportKind(scope),
                client_id=new_client_id,
                sql_text=sql_text,
                output_formats=[OutputFormat(f) for f in output_formats_raw],
                param_declarations=param_declarations,
                uses_time_window=uses_time_window,
                template=None if template_choice == "(default)" else template_choice,
                description=new_description or None,
            )
            yaml_path, sql_path = save_new_report(Path(settings.reports_dir), form)
        except (ValueError, ValidationError, ReportConfigError) as exc:
            st.error(str(exc))
        else:
            st.success(f"Reporte {new_id!r} creado: {yaml_path.name} + {sql_path.name}")
            _load_registries.clear()
            st.rerun()
