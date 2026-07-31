import pytest

from reporting_automation.config.models import OutputFormat, ReportKind
from reporting_automation.exceptions import ReportConfigError
from reporting_automation.report_wizard import (
    WizardInput,
    build_report_config,
    delete_existing_report,
    parse_param_declarations_block,
    save_new_report,
)


def _form(**overrides) -> WizardInput:
    defaults = dict(
        id="clientex_ventas",
        name="VentasClienteX",
        kind=ReportKind.SHARED,
        client_id=None,
        sql_text="SELECT 1 WHERE idCompany = @id_company;\n",
        output_formats=[OutputFormat.CSV],
        param_declarations="id_company:STRING",
        uses_time_window=False,
    )
    defaults.update(overrides)
    return WizardInput(**defaults)


def test_parse_param_declarations_block_parses_multiple_lines():
    schema = parse_param_declarations_block("id_company:STRING\nbilling_month_date:date")
    assert schema == {"id_company": "STRING", "billing_month_date": "DATE"}


def test_parse_param_declarations_block_ignores_blank_lines():
    schema = parse_param_declarations_block("id_company:STRING\n\n   \nstart_date:DATE")
    assert schema == {"id_company": "STRING", "start_date": "DATE"}


def test_parse_param_declarations_block_empty_text_returns_empty_dict():
    assert parse_param_declarations_block("") == {}
    assert parse_param_declarations_block("   \n  \n") == {}


def test_parse_param_declarations_block_raises_with_line_number_on_bad_format():
    with pytest.raises(ValueError, match="Linea 2"):
        parse_param_declarations_block("id_company:STRING\nsin_dos_puntos")


def test_parse_param_declarations_block_raises_on_unsupported_type():
    with pytest.raises(ValueError, match="no soportado"):
        parse_param_declarations_block("x:GEOGRAPHY")


def test_build_report_config_without_time_window():
    report = build_report_config(_form())
    assert report.params_schema == {"id_company": "STRING"}
    assert report.kind == ReportKind.SHARED
    assert report.sql_file == "clientex_ventas.sql"


def test_build_report_config_with_time_window_adds_start_end_date():
    report = build_report_config(_form(uses_time_window=True))
    assert report.params_schema == {
        "id_company": "STRING",
        "start_date": "DATE",
        "end_date": "DATE",
    }


def test_build_report_config_shared_kind_auto_injects_id_company_when_not_declared():
    report = build_report_config(_form(param_declarations=""))
    assert report.params_schema == {"id_company": "STRING"}


def test_build_report_config_shared_kind_keeps_explicit_id_company_type():
    report = build_report_config(_form(param_declarations="id_company:INT64"))
    assert report.params_schema == {"id_company": "INT64"}


def test_build_report_config_custom_kind_keeps_client_id():
    report = build_report_config(
        _form(kind=ReportKind.CUSTOM, client_id="abc123hash", param_declarations="")
    )
    assert report.kind == ReportKind.CUSTOM
    assert report.client_id == "abc123hash"
    assert report.params_schema == {}


def test_save_new_report_writes_yaml_and_sql(tmp_path):
    yaml_path, sql_path = save_new_report(tmp_path, _form())

    assert yaml_path == tmp_path / "shared" / "clientex_ventas.yaml"
    assert sql_path == tmp_path / "shared" / "clientex_ventas.sql"
    assert sql_path.read_text(encoding="utf-8") == "SELECT 1 WHERE idCompany = @id_company;\n"


def test_save_new_report_raises_if_id_already_exists(tmp_path):
    save_new_report(tmp_path, _form())

    with pytest.raises(ReportConfigError):
        save_new_report(tmp_path, _form())


def test_delete_existing_report_removes_files(tmp_path):
    yaml_path, sql_path = save_new_report(tmp_path, _form())
    report = build_report_config(_form())

    deleted_yaml, deleted_sql = delete_existing_report(tmp_path, report)

    assert deleted_yaml == yaml_path
    assert deleted_sql == sql_path
    assert not yaml_path.exists()
    assert not sql_path.exists()


def test_delete_existing_report_raises_if_missing(tmp_path):
    report = build_report_config(_form())

    with pytest.raises(ReportConfigError, match="No existe"):
        delete_existing_report(tmp_path, report)
