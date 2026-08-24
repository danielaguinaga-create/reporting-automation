from __future__ import annotations

import json
import shlex
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
    window: str | None = None
    schedule: str | None = None
    """Cron (formato `gcloud scheduler`) para esta entrada especifica. Si es
    None, `generate_scheduler_jobs`/`build_scheduler_job_command` usan el
    `--schedule` global (ver cli.py) -- no afecta `run_batch`, que corre
    todas las entradas de una sola vez sin importar su frecuencia."""


def load_batch_manifest(path: str | Path) -> list[BatchEntry]:
    """Carga un manifiesto (ej. `config/monthly_batch.yaml`): lista de
    {report, client, params?} a correr en una sola invocacion.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or []
    return [BatchEntry.model_validate(item) for item in raw]


def save_batch_manifest(path: str | Path, entries: list[BatchEntry]) -> None:
    """Persiste el manifiesto -- contraparte de `load_batch_manifest`.

    Preserva el bloque de comentarios inicial del archivo (si existe) para no
    perder la documentacion en cabecera; no preserva comentarios ni el
    espaciado entre entradas individuales, porque `yaml.safe_dump` no lo
    soporta.
    """
    path = Path(path)
    header_lines: list[str] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
            if line.strip() == "" or line.lstrip().startswith("#"):
                header_lines.append(line)
            else:
                break

    payload = [entry.model_dump(mode="json", exclude_defaults=True) for entry in entries]
    body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True) if payload else "[]\n"
    path.write_text("".join(header_lines) + body, encoding="utf-8")


def build_scheduler_job_command(
    entry: BatchEntry,
    *,
    topic_path: str,
    location: str,
    default_schedule: str,
    timezone: str,
    project: str,
) -> str:
    """Arma el comando `gcloud scheduler jobs create pubsub` para una entrada
    del manifiesto -- no lo ejecuta (ver README, seccion Roadmap/Fase 3).

    Usa `entry.schedule` si esta declarado, si no cae a `default_schedule`.
    """
    job_id = f"{entry.report}_{entry.client}"
    message_body = json.dumps(
        {
            "reporte": entry.report,
            "cliente": entry.client,
            "receptores": entry.recipients,
            "params": entry.params,
            "window": entry.window,
        }
    )
    schedule = entry.schedule or default_schedule
    return (
        f"gcloud scheduler jobs create pubsub {job_id} "
        f"--location={location} --schedule={shlex.quote(schedule)} "
        f"--topic={topic_path} --time-zone={shlex.quote(timezone)} "
        f"--message-body={shlex.quote(message_body)} "
        f"--project={project}"
    )


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
            window=entry.window,
        )
        results.append(result)
    return results
