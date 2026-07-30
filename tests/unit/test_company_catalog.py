import pandas as pd

from reporting_automation.company_catalog import fetch_active_companies


class FakeQueryJob:
    def __init__(self, df: pd.DataFrame):
        self._df = df

    def to_dataframe(self, create_bqstorage_client: bool = True) -> pd.DataFrame:
        assert create_bqstorage_client is False
        return self._df


class FakeBigQueryClient:
    def __init__(self, df: pd.DataFrame):
        self._df = df
        self.last_sql = None

    def query(self, sql, job_config=None):
        self.last_sql = sql
        return FakeQueryJob(self._df)


def test_fetch_active_companies_returns_dataframe_as_is():
    expected_df = pd.DataFrame({"idCompany": ["abc123"], "CompanyName": ["Acme"]})
    client = FakeBigQueryClient(expected_df)

    df = fetch_active_companies(client, project="my-project", dataset="03_BaseModel")

    assert df is expected_df


def test_fetch_active_companies_queries_dim_companies_filtered_and_ordered():
    client = FakeBigQueryClient(pd.DataFrame())

    fetch_active_companies(client, project="my-project", dataset="03_BaseModel")

    assert "`my-project.03_BaseModel.DimCompanies`" in client.last_sql
    assert "WHERE CompanyIsActive" in client.last_sql
    assert "ORDER BY CompanyName" in client.last_sql
