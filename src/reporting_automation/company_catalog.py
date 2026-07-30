from __future__ import annotations

from typing import Any, Protocol

import pandas as pd
from google.cloud import bigquery


class QueryRunner(Protocol):
    """Subconjunto de `bigquery.Client` que este modulo necesita (ver
    `query/bigquery_client.py`, mismo patron para poder inyectar un fake en
    tests sin tocar red ni credenciales)."""

    def query(self, sql: str, job_config: bigquery.QueryJobConfig | None = None) -> Any: ...


def fetch_active_companies(client: QueryRunner, project: str, dataset: str) -> pd.DataFrame:
    """Catalogo de companias activas desde `DimCompanies` (idCompany, CompanyName).

    Unica fuente de verdad para el selector de cliente en la UI de
    Streamlit -- reemplaza ahi el uso de `config/clients/*.yaml` (la CLI y
    `run-batch` siguen usando `ClientConfig` sin cambios, ver README).
    """
    sql = (
        f"SELECT idCompany, CompanyName FROM `{project}.{dataset}.DimCompanies` "
        "WHERE CompanyIsActive ORDER BY CompanyName"
    )
    query_job = client.query(sql)
    return query_job.to_dataframe(create_bqstorage_client=False)
