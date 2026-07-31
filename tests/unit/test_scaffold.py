import pytest

from reporting_automation.config.client_registry import ClientRegistry
from reporting_automation.config.models import ClientConfig, OutputFormat, ReportConfig, ReportKind
from reporting_automation.config.registry import ReportRegistry
from reporting_automation.config.scaffold import delete_report, scaffold_client, scaffold_report
from reporting_automation.exceptions import ClientConfigError, ReportConfigError


def _report(**overrides) -> ReportConfig:
    defaults = dict(
        id="clientex_ventas_mensuales",
        name="VentasMensuales",
        kind=ReportKind.CUSTOM,
        client_id="clientex",
        sql_file="clientex_ventas_mensuales.sql",
        output_formats=[OutputFormat.CSV],
        params_schema={"billing_month_date": "DATE"},
        params_defaults={"billing_month_date": "previous_month_first_day"},
        filename_date_param="billing_month_date",
        description="Reporte ad-hoc pedido por ClienteX.",
    )
    defaults.update(overrides)
    return ReportConfig(**defaults)


def test_scaffold_report_writes_yaml_and_sql(tmp_path):
    sql_text = "SELECT * FROM `proj.dataset.table` WHERE d = @billing_month_date;\n"

    yaml_path, sql_path = scaffold_report(tmp_path, _report(), sql_text)

    assert yaml_path == tmp_path / "custom" / "clientex_ventas_mensuales.yaml"
    assert sql_path == tmp_path / "custom" / "clientex_ventas_mensuales.sql"
    assert sql_path.read_text(encoding="utf-8") == sql_text


def test_scaffolded_report_is_discoverable_by_registry(tmp_path):
    sql_text = "SELECT 1;\n"
    scaffold_report(tmp_path, _report(), sql_text)

    registry = ReportRegistry()
    registry.load(tmp_path)

    loaded = registry.get("clientex_ventas_mensuales")
    assert loaded.client_id == "clientex"
    assert loaded.params_schema == {"billing_month_date": "DATE"}
    assert loaded.output_formats == [OutputFormat.CSV]


def test_scaffold_report_shared_kind_uses_shared_dir(tmp_path):
    report = _report(kind=ReportKind.SHARED, client_id=None)
    yaml_path, _ = scaffold_report(tmp_path, report, "SELECT 1;")
    assert yaml_path.parent == tmp_path / "shared"


def test_scaffold_report_refuses_to_overwrite_existing(tmp_path):
    scaffold_report(tmp_path, _report(), "SELECT 1;")

    with pytest.raises(ReportConfigError, match="Ya existe"):
        scaffold_report(tmp_path, _report(), "SELECT 2;")


def test_delete_report_removes_yaml_and_sql(tmp_path):
    yaml_path, sql_path = scaffold_report(tmp_path, _report(), "SELECT 1;")
    assert yaml_path.is_file()
    assert sql_path.is_file()

    deleted_yaml, deleted_sql = delete_report(tmp_path, _report())

    assert deleted_yaml == yaml_path
    assert deleted_sql == sql_path
    assert not yaml_path.exists()
    assert not sql_path.exists()


def test_delete_report_shared_kind_uses_shared_dir(tmp_path):
    report = _report(kind=ReportKind.SHARED, client_id=None)
    yaml_path, _ = scaffold_report(tmp_path, report, "SELECT 1;")

    delete_report(tmp_path, report)

    assert not yaml_path.exists()


def test_delete_report_raises_if_not_found(tmp_path):
    with pytest.raises(ReportConfigError, match="No existe"):
        delete_report(tmp_path, _report())


def test_scaffold_client_writes_yaml(tmp_path):
    client = ClientConfig(
        id="protec", display_name="Protec", bq_params={"id_company": "498cb81c5ba7325f"}
    )

    yaml_path = scaffold_client(tmp_path, client)

    assert yaml_path == tmp_path / "protec.yaml"
    assert yaml_path.is_file()


def test_scaffolded_client_is_discoverable_by_registry(tmp_path):
    client = ClientConfig(
        id="protec", display_name="Protec", bq_params={"id_company": "498cb81c5ba7325f"}
    )
    scaffold_client(tmp_path, client)

    registry = ClientRegistry()
    registry.load(tmp_path)

    loaded = registry.get_or_none("protec")
    assert loaded is not None
    assert loaded.bq_params == {"id_company": "498cb81c5ba7325f"}


def test_scaffold_client_refuses_to_overwrite_existing(tmp_path):
    client = ClientConfig(id="protec", display_name="Protec")
    scaffold_client(tmp_path, client)

    with pytest.raises(ClientConfigError, match="Ya existe"):
        scaffold_client(tmp_path, client)
