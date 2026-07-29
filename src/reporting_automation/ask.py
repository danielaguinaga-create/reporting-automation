from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape as _xml_escape

import pandas as pd
from google.cloud import bigquery

from reporting_automation.config.models import OutputFormat, ReportConfig, ReportKind
from reporting_automation.delivery.base import DeliveryResult
from reporting_automation.delivery.gdrive_delivery import GDriveDelivery
from reporting_automation.llm.nl_answer import summarize_answer
from reporting_automation.llm.schema_introspection import (
    SchemaQueryRunner,
    format_schema_for_prompt,
    get_schema,
)
from reporting_automation.llm.sql_generator import ChatModel, GeneratedSql, generate_sql
from reporting_automation.llm.sql_safety import confirm_statement_type_is_select, validate_readonly_sql
from reporting_automation.rendering.base import RenderContext, RenderedFile
from reporting_automation.rendering.factory import get_renderer
from reporting_automation.rendering.pdf_renderer import build_pdf

_ADHOC_CLIENT_ID = "preguntas_libres"


class AskCancelled(Exception):
    """El usuario no confirmo ejecutar el SQL generado."""


@dataclass
class AskResult:
    question: str
    sql: str
    explanation: str
    answer: str
    df: pd.DataFrame
    rendered_files: list[RenderedFile] = field(default_factory=list)
    bytes_billed_estimate: int | None = None


def _slugify(text: str, max_len: int = 40) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_").lower()
    return slug[:max_len] or "pregunta"


def _ask_report_stub(question: str, formats: list[OutputFormat]) -> ReportConfig:
    """`Renderer`/`Delivery` esperan un `ReportConfig`, pero una pregunta libre
    no tiene uno registrado en `config/reports/` -- se arma uno minimo solo
    para reutilizar la misma tuberia de renderizado/entrega ya probada."""
    slug = _slugify(question)
    return ReportConfig(
        id=f"ask_{slug}",
        name=slug.replace("_", " ").title() or "Pregunta",
        kind=ReportKind.CUSTOM,
        client_id=_ADHOC_CLIENT_ID,
        sql_file="ask.sql",
        output_formats=formats,
    )


def estimate_bytes_billed(bq_client: bigquery.Client, sql: str) -> tuple[int, str | None]:
    """Dry run: no ejecuta la query, solo la valida y estima bytes procesados.

    `statement_type` es la clasificacion que hace BigQuery mismo (no un
    regex nuestro) -- ver `sql_safety.confirm_statement_type_is_select`.
    """
    dry_run_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    job = bq_client.query(sql, job_config=dry_run_config)
    return job.total_bytes_processed, getattr(job, "statement_type", None)


def ask(
    question: str,
    *,
    bq_client: bigquery.Client,
    schema_runner: SchemaQueryRunner,
    chat_model: ChatModel,
    project: str,
    dataset: str,
    output_dir: Path,
    formats: list[OutputFormat],
    schema_cache_path: Path,
    confirm: Callable[[GeneratedSql, int], bool] = lambda generated, num_bytes: True,
    force_refresh_schema: bool = False,
) -> AskResult:
    """Pipeline completo: pregunta en espanol -> SQL -> validacion -> confirmacion
    -> ejecucion -> respuesta en lenguaje natural -> archivos renderizados.

    Nunca escribe en BigQuery: `validate_readonly_sql` rechaza cualquier cosa
    que no sea SELECT/WITH antes de gastar una sola llamada de red, y
    `confirm_statement_type_is_select` repite la verificacion con el
    `statement_type` que devuelve el dry run de BigQuery (autoritativo).
    """
    tables = get_schema(
        schema_runner, project, dataset, schema_cache_path, force_refresh=force_refresh_schema
    )
    schema_text = format_schema_for_prompt(tables)

    generated = generate_sql(question, schema_text, chat_model, project, dataset)
    validate_readonly_sql(generated.sql)

    num_bytes, statement_type = estimate_bytes_billed(bq_client, generated.sql)
    confirm_statement_type_is_select(statement_type)

    if not confirm(generated, num_bytes):
        raise AskCancelled("Ejecucion cancelada por el usuario.")

    query_job = bq_client.query(generated.sql)
    df = query_job.to_dataframe(create_bqstorage_client=False)

    answer = summarize_answer(question, generated.sql, df, chat_model)

    output_dir.mkdir(parents=True, exist_ok=True)
    report_stub = _ask_report_stub(question, formats)
    base_filename = f"{date.today():%Y%m%d}_{_slugify(question)}"
    ctx = RenderContext(base_filename=base_filename, output_dir=output_dir)

    rendered_files: list[RenderedFile] = []
    for fmt in formats:
        if fmt == OutputFormat.PDF:
            path = ctx.output_dir / f"{ctx.base_filename}.pdf"
            build_pdf(
                path,
                title=question,
                df=df,
                extra_paragraphs=[
                    f"<b>SQL generado:</b> {_xml_escape(generated.sql)}",
                    f"<b>Respuesta:</b> {_xml_escape(answer)}",
                ],
            )
            rendered_files.append(RenderedFile(format=OutputFormat.PDF, filename=path.name, local_path=path))
        else:
            rendered_files.append(get_renderer(fmt).render(df, report_stub, ctx))

    return AskResult(
        question=question,
        sql=generated.sql,
        explanation=generated.explanation,
        answer=answer,
        df=df,
        rendered_files=rendered_files,
        bytes_billed_estimate=num_bytes,
    )


def upload_ask_files_to_drive(
    drive_delivery: GDriveDelivery,
    rendered_files: list[RenderedFile],
    question: str,
) -> DeliveryResult:
    """Sube los archivos ya renderizados a `<adhoc_root>/preguntas_libres/<year><mes>/`,
    reutilizando `GDriveDelivery` tal cual (mismo codigo que usa `run --deliver`)."""
    formats = [f.format for f in rendered_files]
    report_stub = _ask_report_stub(question, formats)
    return drive_delivery.send(rendered_files, report_stub, client_id=_ADHOC_CLIENT_ID, recipients=[])
