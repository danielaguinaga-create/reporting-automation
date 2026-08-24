from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from reporting_automation.config.models import DeliveryChannel, OutputFormat, ReportConfig, ReportKind
from reporting_automation.config.scaffold import delete_report, scaffold_report
from reporting_automation.query.bigquery_client import parse_param_declaration

# Convencion ya usada por `orchestrator.resolve_params`/`time_window.py`: un
# reporte que declara estos dos nombres en `params_schema` recibe el picker
# de ventana de tiempo automaticamente al correrlo.
_WINDOW_PARAMS = {"start_date": "DATE", "end_date": "DATE"}

# Un reporte `shared` corre para cualquier cliente via `@id_company` (ver
# CLAUDE.md) -- sin declararlo en `params_schema`, la UI nunca lo bindea y
# BigQuery falla en runtime con "Query parameter 'id_company' not found".
_CLIENT_PARAM = {"id_company": "STRING"}


@dataclass(frozen=True)
class WizardInput:
    id: str
    name: str
    kind: ReportKind
    client_id: str | None
    sql_text: str
    output_formats: list[OutputFormat]
    param_declarations: str
    uses_time_window: bool
    template: str | None = None
    description: str | None = None
    delivery_channels: list[DeliveryChannel] = field(default_factory=list)
    default_recipients: list[str] = field(default_factory=list)


def parse_param_declarations_block(text: str) -> dict[str, str]:
    """Una declaracion `nombre:TIPO_BQ` por linea (ver `parse_param_declaration`);
    lineas vacias se ignoran."""
    schema: dict[str, str] = {}
    for i, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            name, bq_type = parse_param_declaration(line)
        except ValueError as exc:
            raise ValueError(f"Linea {i}: {exc}") from exc
        schema[name] = bq_type
    return schema


def parse_recipients_block(text: str) -> list[str]:
    """Un destinatario por linea; lineas vacias se ignoran."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def build_report_config(form: WizardInput) -> ReportConfig:
    params_schema = parse_param_declarations_block(form.param_declarations)
    if form.kind == ReportKind.SHARED:
        params_schema = {**_CLIENT_PARAM, **params_schema}
    if form.uses_time_window:
        params_schema = {**params_schema, **_WINDOW_PARAMS}

    return ReportConfig(
        id=form.id,
        name=form.name,
        kind=form.kind,
        client_id=form.client_id,
        sql_file=f"{form.id}.sql",
        output_formats=form.output_formats,
        delivery_channels=form.delivery_channels,
        default_recipients=form.default_recipients,
        params_schema=params_schema,
        description=form.description,
        template=form.template,
    )


def save_new_report(reports_dir: Path, form: WizardInput) -> tuple[Path, Path]:
    """Arma el `ReportConfig` a partir del wizard y lo persiste con
    `scaffold_report` -- misma funcion que ya usa `new-report` en la CLI."""
    report = build_report_config(form)
    return scaffold_report(reports_dir, report, form.sql_text)


def delete_existing_report(reports_dir: Path, report: ReportConfig) -> tuple[Path, Path]:
    """Borra el YAML + SQL de un reporte ya registrado."""
    return delete_report(reports_dir, report)
