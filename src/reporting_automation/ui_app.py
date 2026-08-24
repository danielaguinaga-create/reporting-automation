from __future__ import annotations

import os
from datetime import date
from pathlib import Path

os.environ.setdefault("DYLD_LIBRARY_PATH", "/opt/homebrew/lib")

import streamlit as st
from google.cloud import bigquery, storage
from pydantic import ValidationError

from reporting_automation.batch import (
    BatchEntry,
    build_scheduler_job_command,
    load_batch_manifest,
    save_batch_manifest,
)
from reporting_automation.company_catalog import fetch_active_companies
from reporting_automation.config.client_import import slugify
from reporting_automation.config.loader import load_settings
from reporting_automation.config.models import DeliveryChannel, OutputFormat, ReportKind
from reporting_automation.config.registry import ReportRegistry
from reporting_automation.exceptions import ReportConfigError
from reporting_automation.gcs_landing import try_land_rendered_files
from reporting_automation.llm.schema_introspection import get_schema
from reporting_automation.orchestrator import run_report
from reporting_automation.query.bigquery_client import BigQueryExecutor
from reporting_automation.query_builder import (
    AGGREGATE_FUNCTIONS,
    CASE_OPERATORS,
    DATE_BUCKET_GRANULARITIES,
    TEXT_FUNCTIONS,
    CalculatedField,
    CaseFieldSpec,
    DateBucketSpec,
    JoinSpec,
    QueryBuilderSpec,
    TextFieldSpec,
    build_sql,
    suggest_join_columns,
)
from reporting_automation.rendering.template_registry import TemplateRegistry
from reporting_automation.report_wizard import (
    WizardInput,
    delete_existing_report,
    parse_recipients_block,
    save_new_report,
)
from reporting_automation.time_window import WINDOW_PRESETS, resolve_window

_WINDOW_PARAM_NAMES = ("start_date", "end_date")
_CUSTOM_WINDOW_KEY = "custom"
_DATE_COLUMN_TYPES = {"DATE", "DATETIME", "TIMESTAMP"}

_BATCH_MANIFEST_PATH = Path("config/monthly_batch.yaml")

# Presets de frecuencia para la pestaña "Programar reportes" -- cron en
# formato `gcloud scheduler` (ver `batch.build_scheduler_job_command`).
_SCHEDULE_PRESETS: dict[str, str] = {
    "0 6 * * *": "Diario (todos los dias, 6am)",
    "0 6 * * 1": "Semanal (lunes, 6am)",
    "0 6 1 * *": "Mensual (dia 1, 6am)",
    _CUSTOM_WINDOW_KEY: "Personalizado (cron manual)",
}

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


@st.cache_data(ttl=24 * 3600, show_spinner="Leyendo el esquema de BigQuery...")
def _load_bq_schema():
    client = bigquery.Client(project=settings.gcp_project)
    cache_path = Path(".cache") / f"schema_{settings.bigquery_dataset}.json"
    return get_schema(client, settings.gcp_project, settings.bigquery_dataset, cache_path)


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

tab_run, tab_new, tab_schedule = st.tabs(
    ["Correr un reporte", "Crear reporte nuevo", "Programar reportes"]
)

with tab_run:
    report_ids = sorted(r.id for r in registry.list_all())
    if not report_ids:
        st.warning(
            "No hay reportes registrados. Crea uno nuevo en la pestaña "
            "'Crear reporte nuevo'."
        )
    else:
        report_id = st.selectbox("Reporte", report_ids)
        report = registry.get(report_id)
        if report.description:
            st.caption(report.description)

        client_params: dict[str, str] = {}
        client_id: str | None = None
        if report.kind == ReportKind.CUSTOM and report.client_id:
            client_id = report.client_id
            st.text_input("Cliente", value=client_id, disabled=True)
        elif report.kind == ReportKind.CUSTOM and not report.client_id:
            client_id = "general"
        else:
            picked = _company_picker(key="run_client")
            if picked is not None:
                id_company, company_name = picked
                client_id = slugify(company_name)
                client_params = (
                    {"id_company": id_company} if "id_company" in report.params_schema else {}
                )

        if client_id is not None:
            params: dict[str, str] = dict(client_params)
            params_needing_input = {
                name: bq_type
                for name, bq_type in report.params_schema.items()
                if name not in client_params
            }

            if client_params:
                resolved_preview = ", ".join(f"{k}={v}" for k, v in client_params.items())
                st.caption(
                    f"Resuelto automáticamente desde el catálogo de compañías: {resolved_preview}"
                )

            window_fields = [name for name in _WINDOW_PARAM_NAMES if name in params_needing_input]
            other_fields = {
                name: bq_type
                for name, bq_type in params_needing_input.items()
                if name not in window_fields
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
                    gcs_client = storage.Client(project=settings.gcp_project)
                    gcs_uris, gcs_error = try_land_rendered_files(
                        gcs_client, settings.trace_bucket, client_id, result.rendered_files
                    )
                    if gcs_error:
                        st.caption(f"No se pudo copiar a GCS ({settings.trace_bucket}): {gcs_error}")
                    elif gcs_uris:
                        st.caption("Copia de auditoría en GCS: " + ", ".join(gcs_uris))
                    if result.preview is not None:
                        st.caption(
                            f"Vista previa (primeras {len(result.preview)} de {result.rows} filas)"
                        )
                        st.dataframe(result.preview)

with tab_new:
    st.subheader("Crear reporte nuevo")
    st.caption(
        "Da de alta un reporte ad-hoc (YAML + SQL) sin usar la CLI. "
        "La SQL se guarda tal cual la pegues -- no se valida ni se modifica."
    )

    new_name = st.text_input("Nombre", key="wizard_name")
    new_id = slugify(new_name) if new_name else ""
    st.text_input("ID del reporte (autogenerado desde el nombre)", value=new_id, disabled=True)
    new_description = st.text_area("Descripción (opcional)", key="wizard_description")

    is_per_company = st.radio(
        "¿El reporte corresponde a una compañía específica?",
        [True, False],
        format_func=lambda v: (
            "Sí -- necesita un cliente / id_company"
            if v
            else "No -- reporte general, sin cliente asociado"
        ),
        key="wizard_is_per_company",
    )

    new_client_id = None
    if is_per_company:
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
        if scope == "custom":
            picked = _company_picker(key="wizard_client")
            if picked is not None:
                new_client_id, _wizard_company_name = picked
    else:
        scope = "custom"
        st.caption(
            "Se guarda como reporte 'custom' sin cliente asociado -- no se pide "
            "compañía ni se inyecta id_company."
        )

    uses_time_window = st.checkbox(
        "Este reporte usa una ventana de tiempo variable (start_date/end_date)",
        key="wizard_uses_window",
    )
    param_declarations = st.text_area(
        "Variables adicionales (una por línea, formato nombre:TIPO_BQ)",
        help="Tipos válidos: STRING, INT64, FLOAT64, BOOL, DATE, DATETIME, TIMESTAMP. "
        "No hace falta declarar start_date/end_date aquí si activaste la ventana de tiempo arriba.",
        key="wizard_params",
    )
    sql_mode = st.radio(
        "Cómo cargar el SQL",
        ["Escribir a mano", "Constructor visual"],
        format_func=lambda k: k,
        key="wizard_sql_mode",
    )

    if sql_mode == "Constructor visual":
        st.caption(
            "Elegí tablas y columnas, armá los joins entre ellas y agregá campos calculados "
            "si hace falta -- el SQL se arma solo, sin escribir una sola línea."
        )
        try:
            schema_tables = _load_bq_schema()
        except Exception as exc:  # noqa: BLE001 - se reporta, no debe tumbar la pagina
            st.error(f"No se pudo leer el esquema de BigQuery: {exc}")
            schema_tables = []
        tables_by_name = {t.table_name: t for t in schema_tables}
        all_table_names = sorted(tables_by_name)

        qb_tables: list[str] = st.session_state.setdefault("qb_tables", [])
        qb_joins: list[dict] = st.session_state.setdefault("qb_joins", [])
        qb_columns: dict[str, set[str]] = st.session_state.setdefault("qb_columns", {})
        qb_calculated: list[dict] = st.session_state.setdefault("qb_calculated", [])
        qb_date_buckets: list[dict] = st.session_state.setdefault("qb_date_buckets", [])
        qb_case_fields: list[dict] = st.session_state.setdefault("qb_case_fields", [])
        qb_text_fields: list[dict] = st.session_state.setdefault("qb_text_fields", [])

        if not qb_tables:
            base_table = st.selectbox(
                "Tabla principal", all_table_names, key="qb_base_table_choice"
            )
            if st.button("Usar esta tabla"):
                qb_tables.append(base_table)
                qb_columns[base_table] = set()
                st.rerun()
        else:
            st.markdown("**Tablas del reporte**")
            for table_name in qb_tables:
                is_base = table_name == qb_tables[0]
                label = f"📋 {table_name}" + (" (tabla principal)" if is_base else "")
                with st.expander(label):
                    selected = qb_columns.setdefault(table_name, set())
                    for col in tables_by_name[table_name].columns:
                        checked = st.checkbox(
                            f"{col.name} ({col.data_type})",
                            value=col.name in selected,
                            key=f"qb_col_{table_name}_{col.name}",
                        )
                        if checked:
                            selected.add(col.name)
                        else:
                            selected.discard(col.name)

            remaining_tables = [t for t in all_table_names if t not in qb_tables]
            if remaining_tables:
                st.markdown("**Agregar otra tabla (join)**")
                new_table = st.selectbox("Tabla a agregar", remaining_tables, key="qb_new_table")
                join_to = st.selectbox("Unir con", qb_tables, key="qb_join_to")
                suggested = suggest_join_columns(tables_by_name, join_to, new_table)
                left_options = [c.name for c in tables_by_name[join_to].columns]
                right_options = [c.name for c in tables_by_name[new_table].columns]
                default_left = suggested[0] if suggested else left_options[0]
                default_right = suggested[0] if suggested else right_options[0]
                col_a, col_b = st.columns(2)
                with col_a:
                    join_col_left = st.selectbox(
                        f"Columna de {join_to}",
                        left_options,
                        index=left_options.index(default_left),
                        key="qb_join_col_left",
                    )
                with col_b:
                    join_col_right = st.selectbox(
                        f"Columna de {new_table}",
                        right_options,
                        index=right_options.index(default_right),
                        key="qb_join_col_right",
                    )
                join_type = st.radio(
                    "Tipo de join", ["INNER", "LEFT"], key="qb_join_type", horizontal=True
                )
                if st.button("Agregar tabla"):
                    qb_tables.append(new_table)
                    qb_joins.append(
                        {
                            "left_table": join_to,
                            "left_column": join_col_left,
                            "right_table": new_table,
                            "right_column": join_col_right,
                            "join_type": join_type,
                        }
                    )
                    qb_columns[new_table] = set()
                    st.rerun()

            all_picked_columns = [
                (table, col.name) for table in qb_tables for col in tables_by_name[table].columns
            ]

            if is_per_company:
                company_options = [f"{t}.{c}" for t, c in all_picked_columns]
                default_company = next(
                    (opt for opt in company_options if opt.endswith(".idCompany")),
                    company_options[0] if company_options else None,
                )
                if company_options:
                    company_choice = st.selectbox(
                        "Columna de compañía (para filtrar por @id_company)",
                        company_options,
                        index=company_options.index(default_company),
                        key="qb_company_column",
                    )
                    qb_company_table, qb_company_col = company_choice.split(".", 1)
                else:
                    qb_company_table = qb_company_col = None
            else:
                qb_company_table = qb_company_col = None

            qb_time_table = qb_time_col = None
            if uses_time_window:
                date_options = [
                    f"{table}.{col.name}"
                    for table in qb_tables
                    for col in tables_by_name[table].columns
                    if col.data_type in _DATE_COLUMN_TYPES
                ]
                if date_options:
                    time_choice = st.selectbox(
                        "Columna de fecha (para la ventana de tiempo)",
                        date_options,
                        key="qb_time_column",
                    )
                    qb_time_table, qb_time_col = time_choice.split(".", 1)
                else:
                    st.caption(
                        "Ninguna de las tablas agregadas tiene una columna de fecha -- "
                        "agregá una para poder aplicar la ventana de tiempo."
                    )

            st.markdown("**Campos calculados (opcional)**")
            for i, calc in enumerate(qb_calculated):
                col_info, col_action = st.columns([5, 1])
                target = f"{calc['table']}.{calc['column']}" if calc.get("column") else "*"
                with col_info:
                    st.write(f"{calc['function']}({target}) AS {calc['alias']}")
                with col_action:
                    if st.button("Quitar", key=f"qb_remove_calc_{i}"):
                        qb_calculated.pop(i)
                        st.rerun()

            calc_function = st.selectbox(
                "Función", list(AGGREGATE_FUNCTIONS), key="qb_calc_function"
            )
            calc_table = calc_col = None
            if calc_function != "COUNT":
                calc_options = [f"{t}.{c}" for t, c in all_picked_columns]
                if calc_options:
                    calc_choice = st.selectbox(
                        "Sobre la columna", calc_options, key="qb_calc_column"
                    )
                    calc_table, calc_col = calc_choice.split(".", 1)
            calc_alias = st.text_input(
                "Nombre del campo calculado (alias)", key="qb_calc_alias"
            )
            calc_round = None
            if calc_function in {"SUM", "AVG"}:
                calc_round_raw = st.number_input(
                    "Redondear a cuántos decimales (opcional)",
                    min_value=0,
                    max_value=10,
                    value=0,
                    step=1,
                    key="qb_calc_round",
                )
                calc_round = int(calc_round_raw) if calc_round_raw else None
            if st.button("Agregar campo calculado"):
                if not calc_alias.strip():
                    st.error("Ponele un nombre al campo calculado.")
                else:
                    qb_calculated.append(
                        {
                            "function": calc_function,
                            "alias": calc_alias.strip(),
                            "table": calc_table,
                            "column": calc_col,
                            "round_decimals": calc_round,
                        }
                    )
                    st.rerun()

            st.markdown("**Agrupar fecha por período (opcional)**")
            for i, bucket in enumerate(qb_date_buckets):
                col_info, col_action = st.columns([5, 1])
                with col_info:
                    st.write(
                        f"DATE_TRUNC({bucket['table']}.{bucket['column']}, {bucket['granularity']}) "
                        f"AS {bucket['alias']}"
                    )
                with col_action:
                    if st.button("Quitar", key=f"qb_remove_bucket_{i}"):
                        qb_date_buckets.pop(i)
                        st.rerun()

            date_bucket_options = [
                f"{table}.{col.name}"
                for table in qb_tables
                for col in tables_by_name[table].columns
                if col.data_type in _DATE_COLUMN_TYPES
            ]
            if date_bucket_options:
                bucket_choice = st.selectbox(
                    "Columna de fecha a agrupar", date_bucket_options, key="qb_bucket_column"
                )
                bucket_table, bucket_col = bucket_choice.split(".", 1)
                bucket_granularity = st.selectbox(
                    "Agrupar por", list(DATE_BUCKET_GRANULARITIES), key="qb_bucket_granularity"
                )
                bucket_alias = st.text_input("Nombre del campo (alias)", key="qb_bucket_alias")
                if st.button("Agregar agrupación de fecha"):
                    if not bucket_alias.strip():
                        st.error("Ponele un nombre al campo de fecha agrupada.")
                    else:
                        qb_date_buckets.append(
                            {
                                "table": bucket_table,
                                "column": bucket_col,
                                "granularity": bucket_granularity,
                                "alias": bucket_alias.strip(),
                            }
                        )
                        st.rerun()
            else:
                st.caption("Ninguna de las tablas agregadas tiene una columna de fecha.")

            st.markdown("**Campos condicionales -- CASE WHEN (opcional)**")
            for i, case in enumerate(qb_case_fields):
                col_info, col_action = st.columns([5, 1])
                with col_info:
                    st.write(
                        f"CASE WHEN {case['table']}.{case['column']} {case['operator']} "
                        f"'{case['value']}' THEN '{case['then_value']}' ELSE '{case['else_value']}' "
                        f"END AS {case['alias']}"
                    )
                with col_action:
                    if st.button("Quitar", key=f"qb_remove_case_{i}"):
                        qb_case_fields.pop(i)
                        st.rerun()

            if all_picked_columns:
                case_options = [f"{t}.{c}" for t, c in all_picked_columns]
                case_choice = st.selectbox("Columna a evaluar", case_options, key="qb_case_column")
                case_table, case_col = case_choice.split(".", 1)
                case_operator = st.selectbox("Operador", list(CASE_OPERATORS), key="qb_case_operator")
                case_value = st.text_input("Valor de comparación", key="qb_case_value")
                case_then = st.text_input("Si se cumple, mostrar", key="qb_case_then")
                case_else = st.text_input("Si no se cumple, mostrar", key="qb_case_else")
                case_alias = st.text_input("Nombre del campo (alias)", key="qb_case_alias")
                if st.button("Agregar campo condicional"):
                    if not case_alias.strip():
                        st.error("Ponele un nombre al campo condicional.")
                    else:
                        qb_case_fields.append(
                            {
                                "table": case_table,
                                "column": case_col,
                                "operator": case_operator,
                                "value": case_value,
                                "then_value": case_then,
                                "else_value": case_else,
                                "alias": case_alias.strip(),
                            }
                        )
                        st.rerun()

            st.markdown("**Funciones de texto (opcional)**")
            for i, text_field in enumerate(qb_text_fields):
                col_info, col_action = st.columns([5, 1])
                with col_info:
                    if text_field["function"] == "CONCAT":
                        parts = ", ".join(f"{t}.{c}" for t, c in text_field["concat_parts"])
                        st.write(f"CONCAT({parts}) AS {text_field['alias']}")
                    else:
                        st.write(
                            f"{text_field['function']}({text_field['table']}.{text_field['column']}) "
                            f"AS {text_field['alias']}"
                        )
                with col_action:
                    if st.button("Quitar", key=f"qb_remove_text_{i}"):
                        qb_text_fields.pop(i)
                        st.rerun()

            if all_picked_columns:
                text_function = st.selectbox("Función de texto", list(TEXT_FUNCTIONS), key="qb_text_function")
                text_options = [f"{t}.{c}" for t, c in all_picked_columns]
                text_table = text_col = None
                text_concat_parts: list[tuple[str, str]] = []
                if text_function == "CONCAT":
                    text_concat_choice = st.multiselect(
                        "Columnas a combinar (en orden)", text_options, key="qb_text_concat"
                    )
                    text_concat_parts = [tuple(c.split(".", 1)) for c in text_concat_choice]
                else:
                    text_choice = st.selectbox("Sobre la columna", text_options, key="qb_text_column")
                    text_table, text_col = text_choice.split(".", 1)
                text_alias = st.text_input("Nombre del campo (alias)", key="qb_text_alias")
                if st.button("Agregar función de texto"):
                    if not text_alias.strip():
                        st.error("Ponele un nombre al campo de texto.")
                    elif text_function == "CONCAT" and len(text_concat_parts) < 2:
                        st.error("Elegí al menos dos columnas para combinar con CONCAT.")
                    else:
                        qb_text_fields.append(
                            {
                                "function": text_function,
                                "alias": text_alias.strip(),
                                "table": text_table,
                                "column": text_col,
                                "concat_parts": tuple(text_concat_parts),
                            }
                        )
                        st.rerun()

            qb_group_by_selection: list[tuple[str, str]] | None = None
            raw_columns_for_group_by = [
                (table, col) for table in qb_tables for col in sorted(qb_columns.get(table, set()))
            ]
            if qb_calculated and raw_columns_for_group_by:
                st.markdown("**Agrupar por (opcional)**")
                st.caption(
                    "Al agregar un campo calculado, por defecto se agrupa por todas las "
                    "columnas seleccionadas arriba. Destildá las que no quieras usar para "
                    "agrupar -- se van a mostrar igual, con un valor representativo."
                )
                qb_group_by_selection = []
                for table, col in raw_columns_for_group_by:
                    grouped = st.checkbox(
                        f"Agrupar por {table}.{col}",
                        value=True,
                        key=f"qb_group_by_{table}_{col}",
                    )
                    if grouped:
                        qb_group_by_selection.append((table, col))

            col_generate, col_reset = st.columns([1, 1])
            with col_generate:
                if st.button("Generar SQL", type="primary"):
                    try:
                        spec = QueryBuilderSpec(
                            tables=qb_tables,
                            columns={t: sorted(cols) for t, cols in qb_columns.items()},
                            joins=[JoinSpec(**j) for j in qb_joins],
                            filter_by_company=is_per_company,
                            company_table=qb_company_table,
                            company_column=qb_company_col,
                            time_window_table=qb_time_table,
                            time_window_column=qb_time_col,
                            calculated_fields=[CalculatedField(**c) for c in qb_calculated],
                            date_buckets=[DateBucketSpec(**b) for b in qb_date_buckets],
                            case_fields=[CaseFieldSpec(**c) for c in qb_case_fields],
                            text_fields=[TextFieldSpec(**t) for t in qb_text_fields],
                            group_by=qb_group_by_selection,
                        )
                        generated_sql = build_sql(spec, settings.gcp_project, settings.bigquery_dataset)
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state["wizard_sql"] = generated_sql
                        st.rerun()
            with col_reset:
                if st.button("Reiniciar constructor"):
                    for key in (
                        "qb_tables",
                        "qb_joins",
                        "qb_columns",
                        "qb_calculated",
                        "qb_date_buckets",
                        "qb_case_fields",
                        "qb_text_fields",
                    ):
                        st.session_state.pop(key, None)
                    st.rerun()

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

    st.markdown("**Entrega automática (opcional)**")
    st.caption(
        "Sin esto, el reporte solo se puede descargar a mano -- no se manda por correo ni "
        "se sube a Drive, ni corriendo `run --deliver` ni cuando se despliegue Fase 3."
    )
    delivery_channels_raw = st.multiselect(
        "Canales de entrega",
        [c.value for c in DeliveryChannel],
        key="wizard_delivery_channels",
    )
    if DeliveryChannel.FTP.value in delivery_channels_raw:
        st.caption(
            "Aviso: 'ftp' todavía no está implementado (no hay servidor FTP real para "
            "probarlo) -- el reporte queda registrado, pero la entrega fallará para ese canal."
        )
    default_recipients_raw = st.text_area(
        "Destinatarios por defecto (uno por línea, opcional)",
        help="Se usan si se corre/programa este reporte sin destinatarios propios.",
        key="wizard_default_recipients",
    )

    if st.button("Guardar como plantilla", type="primary"):
        try:
            if not new_name:
                raise ValueError("El nombre es obligatorio.")
            if not sql_text.strip():
                raise ValueError("La SQL no puede estar vacía.")
            if not output_formats_raw:
                raise ValueError("Elige al menos un formato de salida.")
            if is_per_company and scope == "custom" and not new_client_id:
                raise ValueError("Elige una compañía para un reporte específico de un cliente.")

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
                delivery_channels=[DeliveryChannel(c) for c in delivery_channels_raw],
                default_recipients=parse_recipients_block(default_recipients_raw),
            )
            yaml_path, sql_path = save_new_report(Path(settings.reports_dir), form)
        except (ValueError, ValidationError, ReportConfigError) as exc:
            st.error(str(exc))
        else:
            st.success(f"Reporte {new_id!r} creado: {yaml_path.name} + {sql_path.name}")
            _load_registries.clear()
            st.rerun()

    st.divider()
    st.subheader("Eliminar reporte existente")
    st.caption("Borra el YAML + SQL de un reporte ya cargado. No se puede deshacer desde la UI.")

    existing_ids = sorted(r.id for r in registry.list_all())
    if not existing_ids:
        st.caption("No hay reportes cargados.")
    else:
        delete_id = st.selectbox("Reporte a eliminar", existing_ids, key="wizard_delete_id")
        report_to_delete = registry.get(delete_id)
        st.caption(
            f"{report_to_delete.kind.value} -- {report_to_delete.description or 'sin descripción'}"
        )
        confirm_id = st.text_input(
            f"Escribe «{delete_id}» para confirmar el borrado",
            key="wizard_delete_confirm",
        )
        if st.button(
            "Eliminar reporte",
            type="secondary",
            disabled=confirm_id != delete_id,
            key="wizard_delete_button",
        ):
            try:
                yaml_path, sql_path = delete_existing_report(
                    Path(settings.reports_dir), report_to_delete
                )
            except ReportConfigError as exc:
                st.error(str(exc))
            else:
                st.success(f"Reporte {delete_id!r} eliminado: {yaml_path.name} + {sql_path.name}")
                _load_registries.clear()
                st.rerun()

with tab_schedule:
    st.subheader("Programar reportes")
    st.caption(
        "Define que reporte corre para que cliente y con que frecuencia. Esto guarda la "
        "programacion en config/monthly_batch.yaml, pero **no ejecuta nada automaticamente "
        "todavia**: Cloud Scheduler -> Pub/Sub -> Cloud Run (Fase 3) esta escrito pero no "
        "desplegado (falta acceso IAM, ver README). El comando `gcloud` que aparece abajo es "
        "el que activaria esta programacion de verdad cuando eso se despliegue."
    )

    batch_entries = (
        load_batch_manifest(_BATCH_MANIFEST_PATH) if _BATCH_MANIFEST_PATH.is_file() else []
    )

    st.markdown("**Reportes programados**")
    if not batch_entries:
        st.caption("No hay reportes programados todavía.")
    else:
        for i, entry in enumerate(batch_entries):
            freq_label = _SCHEDULE_PRESETS.get(entry.schedule, entry.schedule)
            freq_text = freq_label or "Mensual (default de generate-scheduler-jobs, dia 1 6am)"
            window_text = f" — {WINDOW_PRESETS.get(entry.window, entry.window)}" if entry.window else ""
            col_info, col_action = st.columns([5, 1])
            with col_info:
                st.write(f"**{entry.report}** → {entry.client} — {freq_text}{window_text}")
            with col_action:
                if st.button("Quitar", key=f"remove_schedule_{i}"):
                    batch_entries.pop(i)
                    save_batch_manifest(_BATCH_MANIFEST_PATH, batch_entries)
                    st.rerun()

    st.divider()
    st.markdown("**Agregar reporte programado**")

    schedule_report_ids = sorted(r.id for r in registry.list_all())

    if not schedule_report_ids:
        st.caption("No hay reportes registrados.")
    else:
        schedule_report_id = st.selectbox("Reporte", schedule_report_ids, key="schedule_report_id")
        schedule_report = registry.get(schedule_report_id)
        picked = _company_picker(key="schedule_client")
        if picked is not None:
            schedule_id_company, schedule_company_name = picked
            schedule_client_id = slugify(schedule_company_name)
            schedule_params = (
                {"id_company": schedule_id_company}
                if "id_company" in schedule_report.params_schema
                else {}
            )
            schedule_window: str | None = None
            if any(name in schedule_report.params_schema for name in _WINDOW_PARAM_NAMES):
                schedule_window = st.selectbox(
                    "Ventana de tiempo",
                    list(WINDOW_PRESETS.keys()),
                    format_func=lambda k: WINDOW_PRESETS[k],
                    key="schedule_window",
                )
                st.caption(
                    "Se recalcula en cada corrida (ej. 'Mes anterior' siempre resuelve el mes "
                    "anterior a la fecha en que corre, no una fecha fija de hoy)."
                )
            schedule_recipients: list[str] = []
            if schedule_report.delivery_channels:
                channels_text = ", ".join(c.value for c in schedule_report.delivery_channels)
                schedule_recipients_raw = st.text_area(
                    f"Destinatarios para esta programación (uno por línea, canales: {channels_text})",
                    help=(
                        "Dejalo vacío para usar los destinatarios por defecto del reporte "
                        f"({', '.join(schedule_report.default_recipients) or 'ninguno configurado'})."
                    ),
                    key="schedule_recipients",
                )
                schedule_recipients = parse_recipients_block(schedule_recipients_raw)
            else:
                st.caption(
                    "Este reporte no tiene canales de entrega configurados (email/gdrive) -- "
                    "se va a generar y aterrizar en GCS, pero no se le va a mandar a nadie. "
                    "Configuralo desde 'Crear reporte nuevo' si necesitas que se entregue solo."
                )
            freq_choice = st.selectbox(
                "Frecuencia",
                list(_SCHEDULE_PRESETS.keys()),
                format_func=lambda k: _SCHEDULE_PRESETS[k],
                key="schedule_freq",
            )
            custom_cron = ""
            if freq_choice == _CUSTOM_WINDOW_KEY:
                custom_cron = st.text_input(
                    "Cron personalizado (formato gcloud scheduler, ej. '0 6 1 * *')",
                    key="schedule_custom_cron",
                )

            if st.button("Agregar a la programación", type="primary"):
                try:
                    cron = custom_cron.strip() if freq_choice == _CUSTOM_WINDOW_KEY else freq_choice
                    if not cron:
                        raise ValueError("Ingresa una expresión cron válida.")
                    new_entry = BatchEntry(
                        report=schedule_report_id,
                        client=schedule_client_id,
                        params=schedule_params,
                        window=schedule_window,
                        recipients=schedule_recipients,
                        schedule=cron,
                    )
                    batch_entries.append(new_entry)
                    save_batch_manifest(_BATCH_MANIFEST_PATH, batch_entries)
                except (ValueError, ValidationError) as exc:
                    st.error(str(exc))
                else:
                    st.success(
                        f"Agregado: {schedule_report_id!r} → {schedule_client_id!r} "
                        f"({_SCHEDULE_PRESETS.get(cron, cron)})"
                    )
                    command = build_scheduler_job_command(
                        new_entry,
                        topic_path=(
                            f"projects/{settings.gcp_project}/topics/reporting-automation-triggers"
                        ),
                        location="europe-southwest1",
                        default_schedule="0 6 1 * *",
                        timezone="Europe/Madrid",
                        project=settings.gcp_project,
                    )
                    st.caption(
                        "Corre esto (con permisos de IAM) cuando Fase 3 esté desplegada, para "
                        "activar esta programación de verdad:"
                    )
                    st.code(command, language="bash")
