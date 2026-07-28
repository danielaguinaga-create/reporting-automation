from reporting_automation.config.registry import ReportRegistry
from reporting_automation.query.sql_loader import load_sql


def test_load_sql_returns_file_contents_verbatim(reports_fixtures_dir):
    registry = ReportRegistry()
    registry.load(reports_fixtures_dir)

    report = registry.get("sample_report")
    sql_dir = registry.sql_dir_for("sample_report")

    sql = load_sql(report, sql_dir)

    assert "@billing_month_date" in sql
    assert "{billing_month_date}" not in sql
