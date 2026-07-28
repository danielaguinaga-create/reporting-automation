from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from reporting_automation.config.client_registry import ClientRegistry
from reporting_automation.config.registry import ReportRegistry
from reporting_automation.orchestrator import ReportRunResult, run_report
from reporting_automation.query.bigquery_client import BigQueryExecutor


class BatchEntry(BaseModel):
    report: str
    client: str
    params: dict[str, Any] = Field(default_factory=dict)
    recipients: list[str] = Field(default_factory=list)


def load_batch_manifest(path: str | Path) -> list[BatchEntry]:
    """Carga un manifiesto (ej. `config/monthly_batch.yaml`): lista de
    {report, client, params?} a correr en una sola invocacion.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or []
    return [BatchEntry.model_validate(item) for item in raw]


def run_batch(
    entries: list[BatchEntry],
    output_dir: str | Path,
    registry: ReportRegistry,
    executor: BigQueryExecutor,
    client_registry: ClientRegistry | None = None,
    run_date: date | None = None,
) -> list[ReportRunResult]:
    """Corre cada entrada del manifiesto de forma independiente.

    Cada entrada escribe en `output_dir/<client>/`, no en `output_dir`
    directamente: un reporte `shared` (ej. `chats_detalle`) puede aparecer
    varias veces en el manifiesto con distinto `client`, y como el nombre de
    archivo no incluye el cliente, escribir todo en la misma carpeta
    provocaria que la corrida de un cliente sobreescriba silenciosamente la
    de otro.

    Un reporte que falla no detiene a los demas (igual que el resumen final
    del notebook original, que reportaba exitos y errores por separado).
    Es la version local de lo que en Fase 3 dispararia Cloud Scheduler ->
    Pub/Sub por cada linea del manifiesto.
    """
    results = []
    for entry in entries:
        entry_output_dir = Path(output_dir) / entry.client
        result = run_report(
            report_id=entry.report,
            client_id=entry.client,
            params=entry.params,
            output_dir=entry_output_dir,
            registry=registry,
            executor=executor,
            client_registry=client_registry,
            run_date=run_date,
        )
        results.append(result)
    return results
