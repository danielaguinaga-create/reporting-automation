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


def test_shared_report_with_known_client_does_not_ask_for_client_resolved_params():
    """Si el cliente elegido ya trae id_company en su ClientConfig, la UI no
    debe pedirlo como texto -- eso confundia (parecia 'requerido')."""
    at = AppTest.from_file(APP_PATH)
    at.run()
    assert not at.exception

    at.selectbox[0].select("chats_detalle").run()
    at.selectbox[1].select("protec").run()

    assert not at.exception
    assert not any(ti.label.startswith("id_company") for ti in at.text_input)
    assert any("Resuelto autom" in c.value for c in at.caption)


def test_windowed_report_shows_preset_selectbox_not_raw_text_inputs():
    """chats_detalle_rango declara start_date/end_date -- la UI debe ofrecer
    el selector de presets en vez de pedirlos como texto libre."""
    at = AppTest.from_file(APP_PATH)
    at.run()
    assert not at.exception

    at.selectbox[0].select("chats_detalle_rango").run()
    at.selectbox[1].select("protec").run()

    assert not at.exception
    assert not any(ti.label.startswith("start_date") for ti in at.text_input)
    assert not any(ti.label.startswith("end_date") for ti in at.text_input)
    assert len(at.selectbox) == 3  # Reporte, Cliente, Preset de ventana
    assert any("Resuelto autom" in c.value for c in at.caption)


def test_windowed_report_custom_range_shows_date_inputs():
    at = AppTest.from_file(APP_PATH)
    at.run()
    assert not at.exception

    at.selectbox[0].select("chats_detalle_rango").run()
    at.selectbox[1].select("protec").run()
    at.selectbox[2].select("Rango personalizado").run()

    assert not at.exception
    assert len(at.date_input) == 2


def test_running_windowed_report_with_preset_succeeds(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()

    at.selectbox[0].select("chats_detalle_rango").run()
    at.selectbox[1].select("protec").run()
    at.button[0].click().run()

    assert not at.exception
    assert len(at.success) == 1


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
