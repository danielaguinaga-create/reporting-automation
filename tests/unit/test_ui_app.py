from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from reporting_automation.config.models import ReportKind
from reporting_automation.llm.schema_introspection import ColumnInfo, TableSchema

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


class FakeGcsBlob:
    def upload_from_filename(self, path):
        pass


class FakeGcsBucket:
    def blob(self, name):
        return FakeGcsBlob()


class FakeGcsClient:
    def __init__(self, project: str | None = None):
        pass

    def bucket(self, bucket_name):
        return FakeGcsBucket()


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
    monkeypatch.setattr("google.cloud.storage.Client", FakeGcsClient)

    at = AppTest.from_file(APP_PATH)
    at.run()
    assert not at.exception

    at.button[0].click().run()

    assert not at.exception
    assert len(at.success) == 1
    assert len(at.download_button) >= 1
    assert len(at.dataframe) == 1
    assert at.dataframe[0].value["dummy_col"].tolist() == [1, 2, 3]
    gcs_captions = [c.value for c in at.caption if "gs://reporting-automation-trace" in c.value]
    assert len(gcs_captions) == 1


def test_running_a_report_shows_warning_when_gcs_bucket_missing(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    class BoomGcsClient:
        def __init__(self, project=None):
            pass

        def bucket(self, bucket_name):
            raise RuntimeError("404 no existe el bucket")

    monkeypatch.setattr("google.cloud.storage.Client", BoomGcsClient)

    at = AppTest.from_file(APP_PATH)
    at.run()
    at.button[0].click().run()

    assert not at.exception
    assert len(at.success) == 1  # el fallo de GCS no tumba la corrida
    warning_captions = [c.value for c in at.caption if "no se pudo copiar a gcs" in c.value.lower()]
    assert len(warning_captions) == 1


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
    monkeypatch.setattr("google.cloud.storage.Client", FakeGcsClient)

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
    assert len(at.tabs) == 3
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

    at.text_input(key="wizard_name").set_value("Nuevo Reporte Test").run()
    at.text_area(key="wizard_sql").set_value("SELECT 1;").run()

    save_button = next(b for b in at.button if b.label == "Guardar como plantilla")
    save_button.click().run()

    assert not at.exception
    assert len(calls) == 1
    assert calls[0].id == "nuevo_reporte_test"
    assert calls[0].sql_text == "SELECT 1;"
    assert len(at.success) == 1


def test_wizard_is_per_company_defaults_to_true_and_shows_scope_radio(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()

    assert not at.exception
    assert at.radio(key="wizard_is_per_company").value is True
    assert at.radio(key="wizard_scope") is not None


def test_wizard_not_per_company_hides_scope_radio_and_saves_without_client(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    calls = []

    def fake_save_new_report(reports_dir, form):
        calls.append(form)
        return Path("/tmp/fake/custom/x.yaml"), Path("/tmp/fake/custom/x.sql")

    monkeypatch.setattr("reporting_automation.report_wizard.save_new_report", fake_save_new_report)

    at = AppTest.from_file(APP_PATH)
    at.run()

    at.radio(key="wizard_is_per_company").set_value(False).run()

    assert not at.exception
    with pytest.raises(KeyError):
        at.radio(key="wizard_scope")

    at.text_input(key="wizard_name").set_value("Reporte General Test").run()
    at.text_area(key="wizard_sql").set_value("SELECT 1;").run()

    save_button = next(b for b in at.button if b.label == "Guardar como plantilla")
    save_button.click().run()

    assert not at.exception
    assert len(calls) == 1
    assert calls[0].id == "reporte_general_test"
    assert calls[0].kind == ReportKind.CUSTOM
    assert calls[0].client_id is None


def test_wizard_id_field_autogenerates_from_name(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()

    at.text_input(key="wizard_name").set_value("Chats Por Especialidad").run()

    id_display = next(ti for ti in at.text_input if ti.label.startswith("ID del reporte"))
    assert id_display.value == "chats_por_especialidad"
    assert id_display.disabled


def test_wizard_save_without_name_shows_error(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()

    save_button = next(b for b in at.button if b.label == "Guardar como plantilla")
    save_button.click().run()

    assert not at.exception
    assert len(at.error) == 1


def test_delete_section_lists_existing_reports_and_is_disabled_without_confirmation(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()

    assert not at.exception
    delete_selectbox = at.selectbox(key="wizard_delete_id")
    assert "chats_detalle" in delete_selectbox.options

    delete_button = next(b for b in at.button if b.key == "wizard_delete_button")
    assert delete_button.disabled


def test_delete_button_enabled_and_calls_delete_existing_report_on_matching_confirmation(
    monkeypatch,
):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    calls = []

    def fake_delete_existing_report(reports_dir, report):
        calls.append(report.id)
        return Path(f"/tmp/fake/shared/{report.id}.yaml"), Path(f"/tmp/fake/shared/{report.id}.sql")

    monkeypatch.setattr(
        "reporting_automation.report_wizard.delete_existing_report", fake_delete_existing_report
    )

    at = AppTest.from_file(APP_PATH)
    at.run()

    delete_id = at.selectbox(key="wizard_delete_id").value
    at.text_input(key="wizard_delete_confirm").set_value(delete_id).run()

    delete_button = next(b for b in at.button if b.key == "wizard_delete_button")
    assert not delete_button.disabled
    delete_button.click().run()

    assert not at.exception
    assert calls == [delete_id]
    assert len(at.success) == 1


def test_delete_button_disabled_when_confirmation_text_does_not_match(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()

    at.text_input(key="wizard_delete_confirm").set_value("id_incorrecto").run()

    delete_button = next(b for b in at.button if b.key == "wizard_delete_button")
    assert delete_button.disabled


def test_wizard_custom_scope_uses_bigquery_company_picker(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()

    at.radio(key="wizard_scope").set_value("custom").run()

    assert not at.exception
    wizard_client_selectbox = at.selectbox(key="wizard_client")
    assert set(wizard_client_selectbox.options) == {"Protec", "Avanza Seguros"}


def test_schedule_tab_lists_existing_manifest_entries(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)
    from reporting_automation.batch import BatchEntry

    monkeypatch.setattr(
        "reporting_automation.batch.load_batch_manifest",
        lambda path: [BatchEntry(report="chats_detalle", client="protec", schedule="0 6 1 * *")],
    )

    at = AppTest.from_file(APP_PATH)
    at.run()

    assert not at.exception
    schedule_tab_text = " ".join(w.value for w in at.markdown)
    assert "chats_detalle" in schedule_tab_text
    assert "protec" in schedule_tab_text


def test_schedule_tab_add_entry_saves_manifest_and_shows_gcloud_command(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    monkeypatch.setattr("reporting_automation.batch.load_batch_manifest", lambda path: [])
    saved = []
    monkeypatch.setattr(
        "reporting_automation.batch.save_batch_manifest",
        lambda path, entries: saved.append(list(entries)),
    )

    at = AppTest.from_file(APP_PATH)
    at.run()

    at.selectbox(key="schedule_report_id").select("chats_detalle").run()
    at.selectbox(key="schedule_client").select("498cb81c5ba7325f").run()
    at.selectbox(key="schedule_freq").select("0 6 * * *").run()

    add_button = next(b for b in at.button if b.label == "Agregar a la programación")
    add_button.click().run()

    assert not at.exception
    assert len(saved) == 1
    assert saved[0][0].report == "chats_detalle"
    assert saved[0][0].client == "protec"
    assert saved[0][0].schedule == "0 6 * * *"
    assert saved[0][0].params == {"id_company": "498cb81c5ba7325f"}
    assert saved[0][0].window is None
    assert len(at.success) == 1
    assert len(at.code) == 1
    assert "chats_detalle_protec" in at.code[0].value
    with pytest.raises(KeyError):
        at.selectbox(key="schedule_window")


def test_schedule_tab_windowed_report_shows_and_saves_window_preset(monkeypatch):
    """chats_detalle_rango declara start_date/end_date -- programarlo debe
    pedir una ventana de tiempo, para que no falle en silencio al ejecutarse
    (ver gap: nadie resolvia start_date/end_date para reportes programados)."""
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    monkeypatch.setattr("reporting_automation.batch.load_batch_manifest", lambda path: [])
    saved = []
    monkeypatch.setattr(
        "reporting_automation.batch.save_batch_manifest",
        lambda path, entries: saved.append(list(entries)),
    )

    at = AppTest.from_file(APP_PATH)
    at.run()

    at.selectbox(key="schedule_report_id").select("chats_detalle_rango").run()
    at.selectbox(key="schedule_client").select("498cb81c5ba7325f").run()
    at.selectbox(key="schedule_window").select("last_7_days").run()
    at.selectbox(key="schedule_freq").select("0 6 * * *").run()

    add_button = next(b for b in at.button if b.label == "Agregar a la programación")
    add_button.click().run()

    assert not at.exception
    assert len(saved) == 1
    assert saved[0][0].report == "chats_detalle_rango"
    assert saved[0][0].window == "last_7_days"


def test_schedule_tab_client_picker_comes_from_bigquery_not_yaml(monkeypatch):
    """El selector de cliente de 'Programar reportes' tambien viene del
    catalogo de DimCompanies (via BigQuery) -- no de config/clients/*.yaml,
    para que nunca falte una compañía real en ningun punto de la app."""
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    at = AppTest.from_file(APP_PATH)
    at.run()

    assert not at.exception
    schedule_client_selectbox = at.selectbox(key="schedule_client")
    assert set(schedule_client_selectbox.options) == {"Protec", "Avanza Seguros"}


def test_schedule_tab_custom_cron_is_used_when_frequency_is_personalizado(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)

    monkeypatch.setattr("reporting_automation.batch.load_batch_manifest", lambda path: [])
    saved = []
    monkeypatch.setattr(
        "reporting_automation.batch.save_batch_manifest",
        lambda path, entries: saved.append(list(entries)),
    )

    at = AppTest.from_file(APP_PATH)
    at.run()

    at.selectbox(key="schedule_report_id").select("chats_detalle").run()
    at.selectbox(key="schedule_client").select("498cb81c5ba7325f").run()
    at.selectbox(key="schedule_freq").select("custom").run()
    at.text_input(key="schedule_custom_cron").set_value("15 3 * * 2").run()

    add_button = next(b for b in at.button if b.label == "Agregar a la programación")
    add_button.click().run()

    assert not at.exception
    assert saved[0][0].schedule == "15 3 * * 2"


def test_schedule_tab_remove_button_calls_save_with_entry_removed(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)
    from reporting_automation.batch import BatchEntry

    monkeypatch.setattr(
        "reporting_automation.batch.load_batch_manifest",
        lambda path: [BatchEntry(report="chats_detalle", client="protec")],
    )
    saved = []
    monkeypatch.setattr(
        "reporting_automation.batch.save_batch_manifest",
        lambda path, entries: saved.append(list(entries)),
    )

    at = AppTest.from_file(APP_PATH)
    at.run()

    remove_button = next(b for b in at.button if b.label == "Quitar")
    remove_button.click().run()

    assert not at.exception
    assert saved == [[]]


_FAKE_BQ_SCHEMA = [
    TableSchema(
        table_name="DimUsers",
        columns=[
            ColumnInfo(name="idUser", data_type="STRING"),
            ColumnInfo(name="idCompany", data_type="STRING"),
            ColumnInfo(name="UserType", data_type="STRING"),
            ColumnInfo(name="UserSubscribedAtUTC", data_type="TIMESTAMP"),
        ],
    ),
    TableSchema(
        table_name="FactChatConsultations",
        columns=[
            ColumnInfo(name="idUser", data_type="STRING"),
            ColumnInfo(name="ChatSentAtUTC", data_type="TIMESTAMP"),
        ],
    ),
]


def test_wizard_hand_written_mode_is_default_and_skips_schema_lookup(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)
    schema_calls = []
    monkeypatch.setattr(
        "reporting_automation.llm.schema_introspection.get_schema",
        lambda *a, **k: schema_calls.append(1) or _FAKE_BQ_SCHEMA,
    )

    at = AppTest.from_file(APP_PATH)
    at.run()

    assert not at.exception
    assert at.radio(key="wizard_sql_mode").value == "Escribir a mano"
    assert schema_calls == []


def test_visual_builder_pick_base_table_shows_column_checkboxes(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)
    monkeypatch.setattr(
        "reporting_automation.llm.schema_introspection.get_schema", lambda *a, **k: _FAKE_BQ_SCHEMA
    )

    at = AppTest.from_file(APP_PATH)
    at.run()
    at.radio(key="wizard_sql_mode").set_value("Constructor visual").run()
    at.selectbox(key="qb_base_table_choice").select("DimUsers").run()

    use_button = next(b for b in at.button if b.label == "Usar esta tabla")
    use_button.click().run()

    assert not at.exception
    assert any(c.key == "qb_col_DimUsers_UserType" for c in at.checkbox)


def test_visual_builder_generates_sql_for_single_table(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)
    monkeypatch.setattr(
        "reporting_automation.llm.schema_introspection.get_schema", lambda *a, **k: _FAKE_BQ_SCHEMA
    )

    at = AppTest.from_file(APP_PATH)
    at.run()
    at.radio(key="wizard_sql_mode").set_value("Constructor visual").run()
    at.selectbox(key="qb_base_table_choice").select("DimUsers").run()
    next(b for b in at.button if b.label == "Usar esta tabla").click().run()

    at.checkbox(key="qb_col_DimUsers_UserType").set_value(True).run()
    next(b for b in at.button if b.label == "Generar SQL").click().run()

    assert not at.exception
    sql = at.text_area(key="wizard_sql").value
    assert "t0.UserType" in sql
    assert "FROM `data-prd-424213.03_BaseModel.DimUsers` AS t0" in sql


def test_visual_builder_join_suggests_shared_column_and_includes_it_in_sql(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)
    monkeypatch.setattr(
        "reporting_automation.llm.schema_introspection.get_schema", lambda *a, **k: _FAKE_BQ_SCHEMA
    )

    at = AppTest.from_file(APP_PATH)
    at.run()
    at.radio(key="wizard_sql_mode").set_value("Constructor visual").run()
    at.selectbox(key="qb_base_table_choice").select("DimUsers").run()
    next(b for b in at.button if b.label == "Usar esta tabla").click().run()

    at.selectbox(key="qb_new_table").select("FactChatConsultations").run()
    assert at.selectbox(key="qb_join_col_left").value == "idUser"
    assert at.selectbox(key="qb_join_col_right").value == "idUser"
    next(b for b in at.button if b.label == "Agregar tabla").click().run()

    assert not at.exception
    assert any(c.key == "qb_col_FactChatConsultations_ChatSentAtUTC" for c in at.checkbox)

    at.checkbox(key="qb_col_DimUsers_UserType").set_value(True).run()
    at.checkbox(key="qb_col_FactChatConsultations_ChatSentAtUTC").set_value(True).run()
    next(b for b in at.button if b.label == "Generar SQL").click().run()

    assert not at.exception
    sql = at.text_area(key="wizard_sql").value
    assert "INNER JOIN `data-prd-424213.03_BaseModel.FactChatConsultations` AS t1" in sql
    assert "ON t0.idUser = t1.idUser" in sql
    assert "t1.ChatSentAtUTC" in sql


def test_visual_builder_calculated_field_appears_in_generated_sql(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)
    monkeypatch.setattr(
        "reporting_automation.llm.schema_introspection.get_schema", lambda *a, **k: _FAKE_BQ_SCHEMA
    )

    at = AppTest.from_file(APP_PATH)
    at.run()
    at.radio(key="wizard_sql_mode").set_value("Constructor visual").run()
    at.selectbox(key="qb_base_table_choice").select("DimUsers").run()
    next(b for b in at.button if b.label == "Usar esta tabla").click().run()

    at.checkbox(key="qb_col_DimUsers_UserType").set_value(True).run()
    assert at.selectbox(key="qb_calc_function").value == "COUNT"
    at.text_input(key="qb_calc_alias").set_value("Total").run()
    next(b for b in at.button if b.label == "Agregar campo calculado").click().run()

    assert not at.exception
    next(b for b in at.button if b.label == "Generar SQL").click().run()

    sql = at.text_area(key="wizard_sql").value
    assert "COUNT(*) AS Total" in sql
    assert "GROUP BY t0.UserType" in sql


def test_visual_builder_generate_without_columns_shows_error(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)
    monkeypatch.setattr(
        "reporting_automation.llm.schema_introspection.get_schema", lambda *a, **k: _FAKE_BQ_SCHEMA
    )

    at = AppTest.from_file(APP_PATH)
    at.run()
    at.radio(key="wizard_sql_mode").set_value("Constructor visual").run()
    at.selectbox(key="qb_base_table_choice").select("DimUsers").run()
    next(b for b in at.button if b.label == "Usar esta tabla").click().run()

    next(b for b in at.button if b.label == "Generar SQL").click().run()

    assert not at.exception
    assert len(at.error) == 1


def test_visual_builder_reset_clears_state(monkeypatch):
    monkeypatch.setattr("google.cloud.bigquery.Client", FakeBigQueryClient)
    monkeypatch.setattr(
        "reporting_automation.llm.schema_introspection.get_schema", lambda *a, **k: _FAKE_BQ_SCHEMA
    )

    at = AppTest.from_file(APP_PATH)
    at.run()
    at.radio(key="wizard_sql_mode").set_value("Constructor visual").run()
    at.selectbox(key="qb_base_table_choice").select("DimUsers").run()
    next(b for b in at.button if b.label == "Usar esta tabla").click().run()

    next(b for b in at.button if b.label == "Reiniciar constructor").click().run()

    assert not at.exception
    assert at.selectbox(key="qb_base_table_choice") is not None
