from datetime import date

import pandas as pd
import pytest

from reporting_automation.batch import BatchEntry, load_batch_manifest, run_batch
from reporting_automation.config.registry import ReportRegistry


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


def test_load_batch_manifest_parses_entries(tmp_path):
    manifest_path = tmp_path / "batch.yaml"
    manifest_path.write_text(
        "- report: simple_report\n  client: acme\n"
        "- report: sample_report\n  client: acme\n  params:\n    billing_month_date: '2026-01-01'\n"
    )

    entries = load_batch_manifest(manifest_path)

    assert entries == [
        BatchEntry(report="simple_report", client="acme"),
        BatchEntry(report="sample_report", client="acme", params={"billing_month_date": "2026-01-01"}),
    ]


def test_load_batch_manifest_parses_window(tmp_path):
    manifest_path = tmp_path / "batch.yaml"
    manifest_path.write_text("- report: windowed_report\n  client: acme\n  window: previous_month\n")

    entries = load_batch_manifest(manifest_path)

    assert entries == [BatchEntry(report="windowed_report", client="acme", window="previous_month")]


def test_load_batch_manifest_empty_file_returns_empty_list(tmp_path):
    manifest_path = tmp_path / "empty.yaml"
    manifest_path.write_text("")

    assert load_batch_manifest(manifest_path) == []


def test_run_batch_runs_every_entry_independently(tmp_path, registry):
    entries = [
        BatchEntry(report="simple_report", client="acme"),
        BatchEntry(report="does_not_exist", client="acme"),
        BatchEntry(report="simple_report", client="otro_cliente"),
    ]
    executor = FakeExecutor(pd.DataFrame({"dummy_col": [1, 2]}))

    results = run_batch(entries, tmp_path, registry, executor)

    assert [r.status for r in results] == ["success", "failure", "success"]
    assert results[0].client_id == "acme"
    assert results[2].client_id == "otro_cliente"
    assert "does_not_exist" in results[1].error


def test_run_batch_writes_each_client_to_its_own_subdir_no_collision(tmp_path, registry):
    """Un reporte shared corrido para 2 clientes no debe pisar el archivo del otro."""
    entries = [
        BatchEntry(report="simple_report", client="cliente_a"),
        BatchEntry(report="simple_report", client="cliente_b"),
    ]
    executor = FakeExecutor(pd.DataFrame({"dummy_col": [1, 2, 3]}))

    results = run_batch(entries, tmp_path, registry, executor)

    assert results[0].rendered_files[0].local_path == (
        tmp_path / "cliente_a" / results[0].rendered_files[0].filename
    )
    assert results[1].rendered_files[0].local_path == (
        tmp_path / "cliente_b" / results[1].rendered_files[0].filename
    )
    assert results[0].rendered_files[0].local_path.is_file()
    assert results[1].rendered_files[0].local_path.is_file()


def test_run_batch_threads_window_to_run_report(tmp_path, registry):
    entries = [BatchEntry(report="windowed_report", client="acme", window="last_7_days")]
    executor = FakeExecutor(pd.DataFrame({"dummy_col": [1]}))

    results = run_batch(entries, tmp_path, registry, executor, run_date=date(2026, 6, 15))

    assert results[0].status == "success"
    assert executor.last_call["params"] == {"start_date": "2026-06-09", "end_date": "2026-06-15"}
