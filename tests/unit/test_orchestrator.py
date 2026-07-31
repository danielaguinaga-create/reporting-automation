from datetime import date

import pandas as pd
import pytest

from reporting_automation.config.client_registry import ClientRegistry
from reporting_automation.config.registry import ReportRegistry
from reporting_automation.orchestrator import resolve_params, run_report


class FakeExecutor:
    def __init__(self, df: pd.DataFrame):
        self._df = df
        self.last_call = None

    def run(self, sql, params, params_schema):
        self.last_call = {"sql": sql, "params": params, "params_schema": params_schema}
        return self._df


@pytest.fixture
def registry(reports_fixtures_dir) -> ReportRegistry:
    r = ReportRegistry()
    r.load(reports_fixtures_dir)
    return r


@pytest.fixture
def client_registry(clients_fixtures_dir) -> ClientRegistry:
    r = ClientRegistry()
    r.load(clients_fixtures_dir)
    return r


def test_run_report_success_renders_all_formats(tmp_path, registry):
    df = pd.DataFrame({"dummy_col": [1], "billing_month_date": ["2026-05-01"]})
    executor = FakeExecutor(df)

    result = run_report(
        report_id="sample_report",
        client_id="acme",
        params={"billing_month_date": "2026-05-01"},
        output_dir=tmp_path,
        registry=registry,
        executor=executor,
        run_date=date(2026, 6, 15),
    )

    assert result.status == "success"
    assert result.rows == 1
    assert result.columns == 2
    assert {f.local_path.name for f in result.rendered_files} == {
        "202605_MD_acme_SampleReport.csv",
        "202605_MD_acme_SampleReport.xlsx",
    }
    for f in result.rendered_files:
        assert f.local_path.is_file()

    assert executor.last_call["params"] == {"billing_month_date": "2026-05-01"}


def test_run_report_applies_previous_month_default_when_param_missing(tmp_path, registry):
    df = pd.DataFrame({"dummy_col": [1], "billing_month_date": ["x"]})
    executor = FakeExecutor(df)

    result = run_report(
        report_id="sample_report",
        client_id="acme",
        params={},
        output_dir=tmp_path,
        registry=registry,
        executor=executor,
        run_date=date(2026, 6, 15),
    )

    assert result.status == "success"
    assert executor.last_call["params"]["billing_month_date"] == "2026-05-01"


def test_run_report_january_rolls_back_to_previous_december(tmp_path, registry):
    executor = FakeExecutor(pd.DataFrame({"dummy_col": [1], "billing_month_date": ["x"]}))

    run_report(
        report_id="sample_report",
        client_id="acme",
        params={},
        output_dir=tmp_path,
        registry=registry,
        executor=executor,
        run_date=date(2026, 1, 10),
    )

    assert executor.last_call["params"]["billing_month_date"] == "2025-12-01"


def test_run_report_unknown_report_returns_failure(tmp_path, registry):
    executor = FakeExecutor(pd.DataFrame())

    result = run_report(
        report_id="does_not_exist",
        client_id="acme",
        params={},
        output_dir=tmp_path,
        registry=registry,
        executor=executor,
    )

    assert result.status == "failure"
    assert "does_not_exist" in result.error
    assert result.rendered_files == []


def test_run_report_without_params_uses_simple_report(tmp_path, registry):
    executor = FakeExecutor(pd.DataFrame({"dummy_col": [1, 2, 3]}))

    result = run_report(
        report_id="simple_report",
        client_id="acme",
        params={},
        output_dir=tmp_path,
        registry=registry,
        executor=executor,
        run_date=date(2026, 3, 1),
    )

    assert result.status == "success"
    assert result.rows == 3
    assert [f.filename for f in result.rendered_files] == ["202603_MD_acme_SimpleReport.csv"]


def test_resolve_params_leaves_explicit_values_untouched():
    resolved = resolve_params(
        params_schema={"billing_month_date": "DATE"},
        params_defaults={"billing_month_date": "previous_month_first_day"},
        params={"billing_month_date": "2020-01-01"},
        run_date=date(2026, 6, 15),
    )
    assert resolved == {"billing_month_date": "2020-01-01"}


def test_resolve_params_ignores_undeclared_defaults():
    resolved = resolve_params(
        params_schema={},
        params_defaults={"billing_month_date": "previous_month_first_day"},
        params={},
    )
    assert resolved == {}


def test_resolve_params_client_params_override_defaults_but_not_explicit():
    resolved = resolve_params(
        params_schema={"id_company": "STRING", "billing_month_date": "DATE"},
        params_defaults={"billing_month_date": "previous_month_first_day"},
        params={"billing_month_date": "2020-01-01"},
        client_params={"id_company": "abc123", "billing_month_date": "should-not-win"},
        run_date=date(2026, 6, 15),
    )
    assert resolved == {"id_company": "abc123", "billing_month_date": "2020-01-01"}


def test_run_report_injects_client_bq_params(tmp_path, registry, client_registry):
    executor = FakeExecutor(pd.DataFrame({"total": [42]}))

    result = run_report(
        report_id="client_scoped_report",
        client_id="protec",
        params={},
        output_dir=tmp_path,
        registry=registry,
        executor=executor,
        client_registry=client_registry,
    )

    assert result.status == "success"
    assert executor.last_call["params"] == {"id_company": "498cb81c5ba7325f"}


def test_run_report_same_shared_report_uses_different_client_params(tmp_path, registry, client_registry):
    executor = FakeExecutor(pd.DataFrame({"total": [7]}))

    run_report(
        report_id="client_scoped_report",
        client_id="avanza_seguros",
        params={},
        output_dir=tmp_path,
        registry=registry,
        executor=executor,
        client_registry=client_registry,
    )

    assert executor.last_call["params"] == {"id_company": "6336afc65b98ae17"}


def test_run_report_explicit_param_overrides_client_bq_param(tmp_path, registry, client_registry):
    executor = FakeExecutor(pd.DataFrame({"total": [1]}))

    run_report(
        report_id="client_scoped_report",
        client_id="protec",
        params={"id_company": "override-manual"},
        output_dir=tmp_path,
        registry=registry,
        executor=executor,
        client_registry=client_registry,
    )

    assert executor.last_call["params"] == {"id_company": "override-manual"}


def test_run_report_unregistered_client_has_no_bq_params(tmp_path, registry, client_registry):
    executor = FakeExecutor(pd.DataFrame({"total": [0]}))

    result = run_report(
        report_id="client_scoped_report",
        client_id="cliente_sin_registrar",
        params={},
        output_dir=tmp_path,
        registry=registry,
        executor=executor,
        client_registry=client_registry,
    )

    # Sin ClientConfig registrado no se inventa un id_company: el fake
    # executor recibe params vacios. El BigQueryExecutor real rechazaria
    # esto con ValueError por parametro faltante (ver test_bigquery_client).
    assert result.status == "success"
    assert executor.last_call["params"] == {}


def test_resolve_params_window_resolves_start_and_end_date():
    resolved = resolve_params(
        params_schema={"start_date": "DATE", "end_date": "DATE"},
        params_defaults={},
        params={},
        run_date=date(2026, 6, 15),
        window="previous_month",
    )
    assert resolved == {"start_date": "2026-05-01", "end_date": "2026-05-31"}


def test_resolve_params_window_only_fills_declared_fields():
    resolved = resolve_params(
        params_schema={"start_date": "DATE"},
        params_defaults={},
        params={},
        run_date=date(2026, 6, 15),
        window="year_to_date",
    )
    assert resolved == {"start_date": "2026-01-01"}


def test_resolve_params_explicit_param_overrides_window():
    resolved = resolve_params(
        params_schema={"start_date": "DATE", "end_date": "DATE"},
        params_defaults={},
        params={"start_date": "2020-01-01"},
        run_date=date(2026, 6, 15),
        window="previous_month",
    )
    assert resolved == {"start_date": "2020-01-01", "end_date": "2026-05-31"}


def test_resolve_params_client_params_override_window():
    resolved = resolve_params(
        params_schema={"start_date": "DATE", "end_date": "DATE"},
        params_defaults={},
        params={},
        client_params={"start_date": "from-client"},
        run_date=date(2026, 6, 15),
        window="previous_month",
    )
    assert resolved == {"start_date": "from-client", "end_date": "2026-05-31"}


def test_resolve_params_window_raises_when_report_has_no_window_fields():
    with pytest.raises(ValueError, match="no aplica"):
        resolve_params(
            params_schema={"id_company": "STRING"},
            params_defaults={},
            params={},
            run_date=date(2026, 6, 15),
            window="previous_month",
        )


def test_run_report_with_window_resolves_dates(tmp_path, registry):
    executor = FakeExecutor(pd.DataFrame({"dummy_col": [1]}))

    result = run_report(
        report_id="windowed_report",
        client_id="acme",
        params={},
        output_dir=tmp_path,
        registry=registry,
        executor=executor,
        run_date=date(2026, 6, 15),
        window="last_7_days",
    )

    assert result.status == "success"
    assert executor.last_call["params"] == {"start_date": "2026-06-09", "end_date": "2026-06-15"}


def test_run_report_with_unsupported_window_returns_failure(tmp_path, registry):
    executor = FakeExecutor(pd.DataFrame({"dummy_col": [1]}))

    result = run_report(
        report_id="windowed_report",
        client_id="acme",
        params={},
        output_dir=tmp_path,
        registry=registry,
        executor=executor,
        run_date=date(2026, 6, 15),
        window="not_a_real_preset",
    )

    assert result.status == "failure"
    assert "Preset de ventana no soportado" in result.error
