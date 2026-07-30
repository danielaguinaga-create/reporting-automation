from __future__ import annotations

from typing import Any, Protocol

import pandas as pd
from google.cloud import bigquery

SUPPORTED_PARAM_TYPES = {
    "STRING",
    "INT64",
    "FLOAT64",
    "BOOL",
    "DATE",
    "DATETIME",
    "TIMESTAMP",
}


def parse_param_declaration(text: str) -> tuple[str, str]:
    """Parsea una declaracion `nombre:TIPO_BQ` (ej. `billing_month_date:DATE`).

    Usado tanto por `--param` en la CLI (una declaracion por flag) como por
    el textarea de variables del wizard de la UI (una declaracion por
    linea) -- un solo lugar que valida el tipo contra `SUPPORTED_PARAM_TYPES`.
    """
    if ":" not in text:
        raise ValueError(
            f"debe ser nombre:TIPO_BQ (ej. billing_month_date:DATE), recibido: {text!r}"
        )
    name, bq_type = text.split(":", 1)
    name, bq_type = name.strip(), bq_type.strip().upper()
    if bq_type not in SUPPORTED_PARAM_TYPES:
        raise ValueError(
            f"Tipo de parametro no soportado: {bq_type!r} (param {name!r}). "
            f"Soportados: {sorted(SUPPORTED_PARAM_TYPES)}"
        )
    return name, bq_type


class QueryRunner(Protocol):
    """Subconjunto de `bigquery.Client` que este modulo necesita.

    Permite inyectar un fake en tests sin tocar red ni credenciales.
    """

    def query(self, sql: str, job_config: bigquery.QueryJobConfig | None = None) -> Any: ...


class BigQueryExecutor:
    """Ejecuta SQL parametrizado y devuelve un DataFrame.

    A diferencia del notebook (que interpolaba `{billing_month_date}` con
    `str.format`), los parametros se bindean como BigQuery native query
    parameters (`@nombre`), declarados en `ReportConfig.params_schema`
    (ej. `{"billing_month_date": "DATE"}`).
    """

    def __init__(self, client: QueryRunner) -> None:
        self._client = client

    def run(
        self,
        sql: str,
        params: dict[str, Any],
        params_schema: dict[str, str],
    ) -> pd.DataFrame:
        query_parameters = []
        for name, bq_type in params_schema.items():
            bq_type = bq_type.upper()
            if bq_type not in SUPPORTED_PARAM_TYPES:
                raise ValueError(
                    f"Tipo de parametro no soportado: {bq_type!r} (param {name!r}). "
                    f"Soportados: {sorted(SUPPORTED_PARAM_TYPES)}"
                )
            if name not in params:
                raise ValueError(f"Falta el parametro requerido {name!r} para ejecutar la query")
            query_parameters.append(
                bigquery.ScalarQueryParameter(name, bq_type, params[name])
            )

        job_config = bigquery.QueryJobConfig(query_parameters=query_parameters)
        query_job = self._client.query(sql, job_config=job_config)
        return query_job.to_dataframe(create_bqstorage_client=False)
