from __future__ import annotations

from pathlib import Path

from reporting_automation.config.models import ReportConfig


def load_sql(report: ReportConfig, sql_dir: str | Path) -> str:
    """Carga el .sql de un reporte tal cual, sin interpolacion de strings.

    El notebook original usaba `query.format(billing_month_date=...)`, lo cual
    abre una superficie de inyeccion si algun dia el valor viene de un
    parametro externo (ej. el payload de Pub/Sub en Fase 3). Aqui el .sql se
    deja intacto y el binding de parametros ocurre via BigQuery native query
    parameters en `bigquery_client.BigQueryExecutor.run` (placeholders `@nombre`).
    """
    sql_path = Path(sql_dir) / report.sql_file
    return sql_path.read_text(encoding="utf-8")
