from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

APP_PATH = str(
    Path(__file__).resolve().parents[2] / "src" / "reporting_automation" / "ui_app.py"
)

_FAKE_COMPANIES_DF = pd.DataFrame(
    {
        "idCompany": ["498cb81c5ba7325f", "6336afc65b98ae17"],
        "CompanyName": ["Protec", "Avanza Seguros"],
    }
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
        if "DimCompanies" in sql:
            return FakeQueryJob(_FAKE_COMPANIES_DF)
        return FakeQueryJob(pd.DataFrame({"dummy_col": [1, 2, 3]}))


def test_app_renders_report_selector(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

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


def test_shared_report_client_picker_comes_from_bigquery_not_yaml(monkeypatch):
    """El selector de cliente ahora es el catalogo de DimCompanies (via
    BigQuery), no config/clients/*.yaml -- ya no se resuelve por slug."""
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()
    assert not at.exception

    at.selectbox[0].select("chats_detalle").run()
    at.selectbox[1].select("498cb81c5ba7325f").run()

    assert not at.exception
    assert not any(ti.label.startswith("id_company") for ti in at.text_input)
    assert any("Resuelto autom" in c.value for c in at.caption)


def test_windowed_report_shows_preset_selectbox_not_raw_text_inputs(monkeypatch):
    """chats_detalle_rango declara start_date/end_date -- la UI debe ofrecer
    el selector de presets en vez de pedirlos como texto libre."""
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()
    assert not at.exception

    at.selectbox[0].select("chats_detalle_rango").run()
    at.selectbox[1].select("498cb81c5ba7325f").run()

    assert not at.exception
    assert not any(ti.label.startswith("start_date") for ti in at.text_input)
    assert not any(ti.label.startswith("end_date") for ti in at.text_input)
    assert at.selectbox(key="window_preset") is not None
    assert any("Resuelto autom" in c.value for c in at.caption)


def test_windowed_report_custom_range_shows_date_inputs(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()
    assert not at.exception

    at.selectbox[0].select("chats_detalle_rango").run()
    at.selectbox[1].select("498cb81c5ba7325f").run()
    at.selectbox[2].select("Rango personalizado").run()

    assert not at.exception
    assert len(at.date_input) == 2


def test_running_windowed_report_with_preset_succeeds(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()

    at.selectbox[0].select("chats_detalle_rango").run()
    at.selectbox[1].select("498cb81c5ba7325f").run()
    at.button[0].click().run()

    assert not at.exception
    assert len(at.success) == 1


def test_running_a_report_with_orchestrator_failure_shows_error(monkeypatch):
    class BoomBigQueryClient:
        def __init__(self, project: str | None = None):
            pass

        def query(self, sql, job_config=None):
            if "DimCompanies" in sql:
                return FakeQueryJob(_FAKE_COMPANIES_DF)
            raise RuntimeError("boom")

    monkeypatch.setattr("google.cloud.bigquery.Client", BoomBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()
    at.button[0].click().run()

    assert not at.exception
    assert len(at.error) == 1
    assert len(at.success) == 0


def test_wizard_tab_renders_all_fields(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()

    assert not at.exception
    assert len(at.tabs) == 2
    assert len(at.text_area) >= 3  # descripcion, variables, sql
    assert len(at.checkbox) >= 1
    assert len(at.multiselect) >= 1


def test_wizard_save_success_calls_save_new_report_and_reruns(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    calls = []

    def fake_save_new_report(reports_dir, form):
        calls.append(form)
        return Path("/tmp/fake/shared/x.yaml"), Path("/tmp/fake/shared/x.sql")

    monkeypatch.setattr("reporting_automation.report_wizard.save_new_report", fake_save_new_report)

    at = AppTest.from_file(APP_PATH)
    at.run()

    at.text_input(key="wizard_id").set_value("nuevo_reporte_test").run()
    at.text_input(key="wizard_name").set_value("NuevoReporteTest").run()
    at.text_area(key="wizard_sql").set_value("SELECT 1;").run()

    save_button = next(b for b in at.button if b.label == "Guardar como plantilla")
    save_button.click().run()

    assert not at.exception
    assert len(calls) == 1
    assert calls[0].id == "nuevo_reporte_test"
    assert calls[0].sql_text == "SELECT 1;"
    assert len(at.success) == 1


def test_wizard_save_without_id_shows_error(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()

    save_button = next(b for b in at.button if b.label == "Guardar como plantilla")
    save_button.click().run()

    assert not at.exception
    assert len(at.error) == 1


def test_wizard_custom_scope_uses_bigquery_company_picker(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()

    at.radio(key="wizard_scope").set_value("custom").run()

    assert not at.exception
    wizard_client_selectbox = at.selectbox(key="wizard_client")
    assert set(wizard_client_selectbox.options) == {"Protec", "Avanza Seguros"}
