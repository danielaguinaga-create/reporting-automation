from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from reporting_automation.config.client_registry import ClientRegistry
from reporting_automation.config.registry import ReportRegistry
from reporting_automation.query.bigquery_client import BigQueryExecutor
from reporting_automation.query.sql_loader import load_sql
from reporting_automation.rendering.base import RenderContext, RenderedFile, resolve_base_filename
from reporting_automation.rendering.factory import get_renderer

# Sentinels reconocidos en `ReportConfig.params_defaults`. Hoy solo cubre el
# caso del notebook (mes de facturacion anterior). Cloud Scheduler (Fase 3)
# publica el mismo mensaje todos los meses, asi que las fechas se resuelven
# aqui en tiempo de ejecucion en vez de vivir en la config del Scheduler.
_PREVIOUS_MONTH_FIRST_DAY = "previous_month_first_day"


@dataclass(frozen=True)
class ReportRunResult:
    report_id: str
    client_id: str
    status: Literal["success", "failure"]
    rows: int | None = None
    columns: int | None = None
    rendered_files: list[RenderedFile] = field(default_factory=list)
    error: str | None = None


def _previous_month_first_day(run_date: date) -> str:
    year, month = run_date.year, run_date.month
    if month == 1:
        year, month = year - 1, 12
    else:
        month -= 1
    return date(year, month, 1).isoformat()


def resolve_params(
    params_schema: dict[str, str],
    params_defaults: dict[str, str],
    params: dict[str, Any],
    client_params: dict[str, Any] | None = None,
    run_date: date | None = None,
) -> dict[str, Any]:
    """Resuelve los params finales para una corrida, en orden de prioridad:

    1. `params` explicitos (ej. `--param` en la CLI) — siempre ganan.
    2. `client_params` del `ClientConfig` del cliente (ej. su `id_company`),
       para que reportes `shared` no requieran pegar ese valor a mano.
    3. `params_defaults` del reporte (con sentinels como
       `previous_month_first_day` resueltos contra `run_date`).
    """
    run_date = run_date or date.today()
    client_params = client_params or {}

    resolved: dict[str, Any] = {}
    for name in params_schema:
        if name in params_defaults:
            default = params_defaults[name]
            resolved[name] = (
                _previous_month_first_day(run_date)
                if default == _PREVIOUS_MONTH_FIRST_DAY
                else default
            )
    resolved.update(client_params)
    resolved.update(params)
    return resolved


def run_report(
    report_id: str,
    client_id: str,
    params: dict[str, Any],
    output_dir: str | Path,
    registry: ReportRegistry,
    executor: BigQueryExecutor,
    client_registry: ClientRegistry | None = None,
    run_date: date | None = None,
) -> ReportRunResult:
    """El "Def Main" del diagrama, acotado a Fase 1.

    Resuelve el reporte, ejecuta la query en BigQuery y renderiza cada
    `output_format` a disco local. Delivery (email/FTP/GDrive), Secret
    Manager y tracing (GCS + GDrive) son responsabilidad de Fase 2/3 — no se
    invocan aqui todavia (ver README, seccion Roadmap).

    Si `client_registry` tiene un `ClientConfig` para `client_id`, sus
    `bq_params` (ej. `id_company`) se inyectan automaticamente para
    cualquier nombre que el reporte declare en `params_schema`.
    """
    try:
        report = registry.get(report_id)
        sql_dir = registry.sql_dir_for(report_id)

        client_config = client_registry.get_or_none(client_id) if client_registry else None
        client_params = client_config.bq_params if client_config else {}

        resolved_params = resolve_params(
            report.params_schema, report.params_defaults, params, client_params, run_date
        )

        sql = load_sql(report, sql_dir)
        df = executor.run(sql, resolved_params, report.params_schema)

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        base_filename = resolve_base_filename(report, resolved_params, run_date)
        ctx = RenderContext(
            base_filename=base_filename,
            output_dir=output_dir,
            client_id=client_id,
            client_display_name=client_config.display_name if client_config else None,
            client_branding=client_config.branding if client_config else {},
            resolved_params=resolved_params,
            generated_at=run_date or date.today(),
        )

        rendered_files = [
            get_renderer(fmt).render(df, report, ctx) for fmt in report.output_formats
        ]

        return ReportRunResult(
            report_id=report_id,
            client_id=client_id,
            status="success",
            rows=df.shape[0],
            columns=df.shape[1],
            rendered_files=rendered_files,
        )
    except Exception as exc:  # noqa: BLE001 - se reporta al caller, no se traga
        return ReportRunResult(
            report_id=report_id,
            client_id=client_id,
            status="failure",
            error=str(exc),
        )
