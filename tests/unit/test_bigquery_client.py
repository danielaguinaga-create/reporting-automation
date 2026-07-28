from datetime import date

import pandas as pd
import pytest

from reporting_automation.query.bigquery_client import BigQueryExecutor


class FakeQueryJob:
    def __init__(self, df: pd.DataFrame):
        self._df = df

    def to_dataframe(self, create_bqstorage_client: bool = True) -> pd.DataFrame:
        assert create_bqstorage_client is False
        return self._df


class FakeBigQueryClient:
    """Sustituye a google.cloud.bigquery.Client: no toca red ni credenciales."""

    def __init__(self, df: pd.DataFrame):
        self._df = df
        self.last_sql = None
        self.last_job_config = None

    def query(self, sql, job_config=None):
        self.last_sql = sql
        self.last_job_config = job_config
        return FakeQueryJob(self._df)


def test_run_executes_sql_and_returns_dataframe():
    expected_df = pd.DataFrame({"a": [1, 2]})
    fake_client = FakeBigQueryClient(expected_df)
    executor = BigQueryExecutor(fake_client)

    df = executor.run(
        sql="SELECT @billing_month_date AS d",
        params={"billing_month_date": "2026-06-01"},
        params_schema={"billing_month_date": "DATE"},
    )

    assert df is expected_df
    assert fake_client.last_sql == "SELECT @billing_month_date AS d"

    bound = {p.name: (p.type_, p.value) for p in fake_client.last_job_config.query_parameters}
    assert bound == {"billing_month_date": ("DATE", date(2026, 6, 1))}


def test_run_with_no_params_schema_binds_nothing():
    fake_client = FakeBigQueryClient(pd.DataFrame())
    executor = BigQueryExecutor(fake_client)

    executor.run(sql="SELECT 1", params={}, params_schema={})

    assert fake_client.last_job_config.query_parameters == []


def test_run_raises_on_missing_param():
    executor = BigQueryExecutor(FakeBigQueryClient(pd.DataFrame()))

    with pytest.raises(ValueError, match="Falta el parametro"):
        executor.run(sql="SELECT @x", params={}, params_schema={"x": "STRING"})


def test_run_raises_on_unsupported_type():
    executor = BigQueryExecutor(FakeBigQueryClient(pd.DataFrame()))

    with pytest.raises(ValueError, match="no soportado"):
        executor.run(sql="SELECT @x", params={"x": "1"}, params_schema={"x": "GEOGRAPHY"})
