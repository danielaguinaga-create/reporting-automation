from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

APP_PATH = str(
    Path(__file__).resolve().parents[2] / "src" / "reporting_automation" / "ui_app.py"
)


class FakeQueryJob:
    def __init__(self, df: pd.DataFrame):
        self._df = df

    def to_dataframe(self, create_bqstorage_client: bool = True) -> pd.DataFrame:
        return self._df


class FakeBigQueryClient:
    def __init__(self, project: str | None = None):
        pass

    def query(self, sql, job_config=None):
        return FakeQueryJob(pd.DataFrame({"dummy_col": [1, 2, 3]}))


def test_app_renders_report_selector():
    at = AppTest.from_file(APP_PATH)
    at.run()

    assert not at.exception
    assert at.title[0].value == "Reporting Automation"
    assert len(at.selectbox) >= 1
    assert len(at.selectbox[0].options) > 0


def test_running_a_report_shows_success_and_download_button(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()
    assert not at.exception

    at.button[0].click().run()

    assert not at.exception
    assert len(at.success) == 1
    assert len(at.download_button) >= 1


def test_running_a_report_with_orchestrator_failure_shows_error(monkeypatch):
    class BoomBigQueryClient:
        def __init__(self, project: str | None = None):
            pass

        def query(self, sql, job_config=None):
            raise RuntimeError("boom")

    monkeypatch.setattr("google.cloud.bigquery.Client", BoomBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()
    at.button[0].click().run()

    assert not at.exception
    assert len(at.error) == 1
    assert len(at.success) == 0
