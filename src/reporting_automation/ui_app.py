from __future__ import annotations

import os
from datetime import date
from pathlib import Path

os.environ.setdefault("DYLD_LIBRARY_PATH", "/opt/homebrew/lib")

import streamlit as st
from google.cloud import bigquery
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
from reporting_automation.gcs_landing import try_land_rendered_files_for_project
from reporting_automation.orchestrator import run_report
from reporting_automation.query.bigquery_client import BigQueryExecutor
from reporting_automation.report_wizard import (
    WizardInput,
    delete_existing_report,
    parse_recipients_block,
    save_new_report,
)
from reporting_automation.time_window import WINDOW_PRESETS, resolve_window

_WINDOW_PARAM_NAMES = ("start_date", "end_date")
_CUSTOM_WINDOW_KEY = "custom"

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
    "Esta pantalla nunca manda correos ni sube a Drive -- eso solo pasa desde la CLI "
    "(`run --deliver`) o, cuando se despliegue, automaticamente por Fase 3 (Pub/Sub)."
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
                    st.session_state["run_result_cache"] = None
                    st.error(f"Error ejecutando {report_id!r}: {result.error}")
                else:
                    gcs_uris, gcs_error = try_land_rendered_files_for_project(
                        settings.gcp_project, settings.trace_bucket, client_id, result.rendered_files
                    )
                    st.session_state["run_result_cache"] = {
                        "rows": result.rows,
                        "columns": result.columns,
                        "rendered_files": result.rendered_files,
                        "preview": result.preview,
                        "gcs_uris": gcs_uris,
                        "gcs_error": gcs_error,
                    }

            # Fuera del if del boton: un click en "Descargar ..." abajo es en
            # si mismo otro rerun completo de Streamlit, en el que el boton
            # "Ejecutar reporte" vuelve a evaluar False. Sin cachear el
            # resultado en session_state, ese click hacia "desaparecer" el
            # resto de los botones de descarga -- y para recuperarlos habia
            # que tocar "Ejecutar reporte" de nuevo, re-corriendo la query
            # real contra BigQuery y volviendo a subir todo a GCS.
            cached_result = st.session_state.get("run_result_cache")
            if cached_result is not None:
                st.success(f"OK: {cached_result['rows']} filas x {cached_result['columns']} columnas")
                for rendered in cached_result["rendered_files"]:
                    st.download_button(
                        label=f"Descargar {rendered.filename}",
                        data=rendered.local_path.read_bytes(),
                        file_name=rendered.filename,
                        key=f"download_{rendered.filename}",
                    )
                if cached_result["gcs_error"]:
                    st.caption(
                        f"No se pudo copiar a GCS ({settings.trace_bucket}): {cached_result['gcs_error']}"
                    )
                elif cached_result["gcs_uris"]:
                    st.caption("Copia de auditoría en GCS: " + ", ".join(cached_result["gcs_uris"]))
                if cached_result["preview"] is not None:
                    st.caption(
                        f"Vista previa (primeras {len(cached_result['preview'])} de "
                        f"{cached_result['rows']} filas)"
                    )
                    st.dataframe(cached_result["preview"])

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
                _wizard_id_company, wizard_company_name = picked
                new_client_id = slugify(wizard_company_name)
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

        schedule_client_id: str | None = None
        schedule_id_company: str | None = None
        if schedule_report.kind == ReportKind.CUSTOM:
            schedule_client_id = schedule_report.client_id or "general"
        else:
            picked = _company_picker(key="schedule_client")
            if picked is not None:
                schedule_id_company, schedule_company_name = picked
                schedule_client_id = slugify(schedule_company_name)

        if schedule_client_id is not None:
            schedule_params = (
                {"id_company": schedule_id_company}
                if schedule_id_company and "id_company" in schedule_report.params_schema
                else {}
            )
            # Las keys de estos dos widgets incluyen schedule_report_id: sin
            # eso, cambiar de reporte sin todavia haber agregado la entrada
            # deja el valor tipeado para el reporte anterior pegado en
            # session_state y lo termina guardando para el reporte nuevo.
            schedule_window: str | None = None
            if any(name in schedule_report.params_schema for name in _WINDOW_PARAM_NAMES):
                schedule_window = st.selectbox(
                    "Ventana de tiempo",
                    list(WINDOW_PRESETS.keys()),
                    format_func=lambda k: WINDOW_PRESETS[k],
                    key=f"schedule_window_{schedule_report_id}",
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
                    key=f"schedule_recipients_{schedule_report_id}",
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
