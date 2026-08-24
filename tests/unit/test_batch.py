from datetime import date

import pandas as pd
import pytest

from reporting_automation.batch import (
    BatchEntry,
    build_scheduler_job_command,
    load_batch_manifest,
    run_batch,
    save_batch_manifest,
)
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


def test_save_batch_manifest_round_trips_entries(tmp_path):
    manifest_path = tmp_path / "batch.yaml"
    entries = [
        BatchEntry(report="simple_report", client="acme"),
        BatchEntry(report="windowed_report", client="acme", window="previous_month", schedule="0 6 1 * *"),
    ]

    save_batch_manifest(manifest_path, entries)

    assert load_batch_manifest(manifest_path) == entries


def test_save_batch_manifest_preserves_leading_comment_header(tmp_path):
    manifest_path = tmp_path / "batch.yaml"
    manifest_path.write_text(
        "# Manifiesto de la corrida mensual.\n# Segunda linea de comentario.\n\n"
        "- report: simple_report\n  client: acme\n"
    )

    save_batch_manifest(manifest_path, [BatchEntry(report="simple_report", client="otro")])

    content = manifest_path.read_text()
    assert content.startswith(
        "# Manifiesto de la corrida mensual.\n# Segunda linea de comentario.\n\n"
    )
    assert load_batch_manifest(manifest_path) == [BatchEntry(report="simple_report", client="otro")]


def test_save_batch_manifest_empty_list_writes_empty_yaml_list(tmp_path):
    manifest_path = tmp_path / "batch.yaml"

    save_batch_manifest(manifest_path, [])

    assert load_batch_manifest(manifest_path) == []


def test_build_scheduler_job_command_uses_default_schedule_when_entry_has_none():
    entry = BatchEntry(report="chats_detalle", client="protec")

    command = build_scheduler_job_command(
        entry,
        topic_path="projects/proj/topics/triggers",
        location="europe-southwest1",
        default_schedule="0 6 1 * *",
        timezone="Europe/Madrid",
        project="proj",
    )

    assert "chats_detalle_protec" in command
    assert "--schedule='0 6 1 * *'" in command
    assert "--project=proj" in command


def test_build_scheduler_job_command_prefers_entry_schedule_over_default():
    entry = BatchEntry(report="chats_detalle", client="protec", schedule="0 8 * * 1")

    command = build_scheduler_job_command(
        entry,
        topic_path="projects/proj/topics/triggers",
        location="europe-southwest1",
        default_schedule="0 6 1 * *",
        timezone="Europe/Madrid",
        project="proj",
    )

    assert "--schedule='0 8 * * 1'" in command


def test_build_scheduler_job_command_includes_window_in_message_body():
    """El scheduler dispara Pub/Sub -> Cloud Run (Fase 3, ver main_entrypoint.py)
    -- sin esto, un reporte programado con ventana de tiempo perdia el preset
    al pasar por Cloud Scheduler, aunque run_batch (CLI) si lo resolviera bien."""
    entry = BatchEntry(report="windowed_report", client="acme", window="last_7_days")

    command = build_scheduler_job_command(
        entry,
        topic_path="projects/proj/topics/triggers",
        location="europe-southwest1",
        default_schedule="0 6 1 * *",
        timezone="Europe/Madrid",
        project="proj",
    )

    assert '"window": "last_7_days"' in command


def test_run_batch_threads_window_to_run_report(tmp_path, registry):
    entries = [BatchEntry(report="windowed_report", client="acme", window="last_7_days")]
    executor = FakeExecutor(pd.DataFrame({"dummy_col": [1]}))

    results = run_batch(entries, tmp_path, registry, executor, run_date=date(2026, 6, 15))

    assert results[0].status == "success"
    assert executor.last_call["params"] == {"start_date": "2026-06-09", "end_date": "2026-06-15"}
