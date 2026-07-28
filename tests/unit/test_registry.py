import pytest

from reporting_automation.config.models import ReportKind
from reporting_automation.config.registry import ReportRegistry
from reporting_automation.exceptions import ReportConfigError, ReportNotFoundError


def test_load_discovers_reports(reports_fixtures_dir):
    registry = ReportRegistry()
    registry.load(reports_fixtures_dir)

    ids = {r.id for r in registry.list_all()}
    assert {"sample_report", "simple_report"} <= ids


def test_get_returns_expected_report(reports_fixtures_dir):
    registry = ReportRegistry()
    registry.load(reports_fixtures_dir)

    report = registry.get("sample_report")
    assert report.kind == ReportKind.CUSTOM
    assert report.client_id == "acme"
    assert report.sql_file == "sample_report.sql"
    assert report.params_schema == {"billing_month_date": "DATE"}


def test_get_unknown_report_raises(reports_fixtures_dir):
    registry = ReportRegistry()
    registry.load(reports_fixtures_dir)

    with pytest.raises(ReportNotFoundError):
        registry.get("does_not_exist")


def test_sql_dir_for_points_to_yaml_parent(reports_fixtures_dir):
    registry = ReportRegistry()
    registry.load(reports_fixtures_dir)

    sql_dir = registry.sql_dir_for("simple_report")
    assert (sql_dir / "simple_report.sql").is_file()


def test_list_by_client_filters(reports_fixtures_dir):
    registry = ReportRegistry()
    registry.load(reports_fixtures_dir)

    reports = registry.list_by_client("acme")
    assert {r.id for r in reports} == {"sample_report", "simple_report"}
    assert registry.list_by_client("nobody") == []


def test_duplicate_report_id_raises(tmp_path):
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()
    yaml_body = (
        "id: dup\nname: Dup\nkind: custom\nclient_id: acme\n"
        "sql_file: dup.sql\noutput_formats: [csv]\n"
    )
    (custom_dir / "dup.sql").write_text("SELECT 1;")
    (custom_dir / "a.yaml").write_text(yaml_body)
    (custom_dir / "b.yaml").write_text(yaml_body)

    registry = ReportRegistry()
    with pytest.raises(ReportConfigError, match="duplicado"):
        registry.load(tmp_path)


def test_missing_sql_file_raises(tmp_path):
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()
    (custom_dir / "broken.yaml").write_text(
        "id: broken\nname: Broken\nkind: custom\nclient_id: acme\n"
        "sql_file: missing.sql\noutput_formats: [csv]\n"
    )

    registry = ReportRegistry()
    with pytest.raises(ReportConfigError, match="no existe"):
        registry.load(tmp_path)


def test_invalid_config_raises(tmp_path):
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()
    (custom_dir / "invalid.yaml").write_text("name: MissingRequiredFields\n")

    registry = ReportRegistry()
    with pytest.raises(ReportConfigError, match="invalida"):
        registry.load(tmp_path)
