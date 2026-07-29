from pathlib import Path

import pandas as pd
import pytest

from reporting_automation.ask import AskCancelled, ask, upload_ask_files_to_drive
from reporting_automation.config.models import OutputFormat
from reporting_automation.delivery.base import DeliveryResult
from reporting_automation.llm.sql_safety import UnsafeSqlError
from reporting_automation.rendering.base import RenderedFile


class FakeQueryJob:
    def __init__(self, *, total_bytes_processed=0, statement_type="SELECT", df=None):
        self.total_bytes_processed = total_bytes_processed
        self.statement_type = statement_type
        self._df = df

    def to_dataframe(self, create_bqstorage_client=False):
        return self._df


class FakeSchemaRunner:
    def __init__(self, rows=None):
        self._rows = rows or [
            {"table_name": "usuarios", "column_name": "UserToken", "data_type": "STRING"},
        ]
        self.queries: list[str] = []

    def query(self, sql):
        self.queries.append(sql)
        return self._rows


class FakeBigQueryClient:
    """`bq_client`: dry run (costo/statement_type) + ejecucion real de la query final."""

    def __init__(self, df, total_bytes_processed=123, statement_type="SELECT"):
        self._df = df
        self._total_bytes_processed = total_bytes_processed
        self._statement_type = statement_type
        self.queries: list[tuple[str, object]] = []

    def query(self, sql, job_config=None):
        self.queries.append((sql, job_config))
        if job_config is not None and getattr(job_config, "dry_run", False):
            return FakeQueryJob(
                total_bytes_processed=self._total_bytes_processed, statement_type=self._statement_type
            )
        return FakeQueryJob(df=self._df)


class ScriptedChatModel:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def generate(self, *, system, user):
        self.calls.append({"system": system, "user": user})
        return self._responses.pop(0)


SQL_RESPONSE = "```sql\nSELECT COUNT(*) AS total FROM `proj.ds.usuarios`\n```\nCuenta usuarios."
ANSWER_RESPONSE = "Hay 3 usuarios registrados."


def _ask_kwargs(tmp_path, bq_client, chat_model, confirm=lambda g, n: True, formats=None, schema_runner=None):
    return dict(
        bq_client=bq_client,
        schema_runner=schema_runner or FakeSchemaRunner(),
        chat_model=chat_model,
        project="proj",
        dataset="ds",
        output_dir=tmp_path / "out",
        formats=formats or [OutputFormat.CSV],
        schema_cache_path=tmp_path / "schema.json",
        confirm=confirm,
    )


def test_ask_happy_path_renders_all_formats_and_answers(tmp_path):
    df = pd.DataFrame({"total": [3]})
    bq_client = FakeBigQueryClient(df)
    chat_model = ScriptedChatModel([SQL_RESPONSE, ANSWER_RESPONSE])
    all_formats = [OutputFormat.CSV, OutputFormat.XLSX, OutputFormat.PDF]

    result = ask(
        "cuantos usuarios hay",
        **_ask_kwargs(tmp_path, bq_client, chat_model, formats=all_formats),
    )

    assert result.sql == "SELECT COUNT(*) AS total FROM `proj.ds.usuarios`"
    assert result.answer == ANSWER_RESPONSE
    assert result.bytes_billed_estimate == 123
    assert {f.format for f in result.rendered_files} == set(all_formats)
    for rendered in result.rendered_files:
        assert rendered.local_path.is_file()


def test_ask_cancelled_when_confirm_returns_false(tmp_path):
    df = pd.DataFrame({"total": [3]})
    bq_client = FakeBigQueryClient(df)
    chat_model = ScriptedChatModel([SQL_RESPONSE, ANSWER_RESPONSE])
    kwargs = _ask_kwargs(tmp_path, bq_client, chat_model, confirm=lambda g, n: False)

    with pytest.raises(AskCancelled):
        ask("cuantos usuarios hay", **kwargs)

    # Solo hubo el dry run de costo -- la query real (una segunda llamada a
    # bq_client.query) nunca se dispara si el usuario cancela.
    assert len(bq_client.queries) == 1
    assert bq_client.queries[0][1].dry_run is True


def test_ask_rejects_unsafe_sql_before_touching_bigquery(tmp_path):
    unsafe_response = "```sql\nDROP TABLE `proj.ds.usuarios`\n```\nBorra la tabla."
    bq_client = FakeBigQueryClient(pd.DataFrame())
    chat_model = ScriptedChatModel([unsafe_response])

    with pytest.raises(UnsafeSqlError):
        ask("borra los usuarios", **_ask_kwargs(tmp_path, bq_client, chat_model))

    # validate_readonly_sql corre antes que cualquier query a BigQuery (ni dry run).
    assert bq_client.queries == []


def test_ask_rejects_when_bigquery_dry_run_reports_non_select(tmp_path):
    bq_client = FakeBigQueryClient(pd.DataFrame(), statement_type="DELETE")
    chat_model = ScriptedChatModel([SQL_RESPONSE])

    with pytest.raises(UnsafeSqlError):
        ask("cuantos usuarios hay", **_ask_kwargs(tmp_path, bq_client, chat_model))


class FakeDriveDelivery:
    def __init__(self, result: DeliveryResult):
        self._result = result
        self.last_call: dict | None = None

    def send(self, files, report, client_id, recipients):
        self.last_call = {"files": files, "report": report, "client_id": client_id, "recipients": recipients}
        return self._result


def test_upload_ask_files_to_drive_uses_dedicated_client_id(tmp_path):
    expected = DeliveryResult(channel="gdrive", status="sent", detail="https://drive/x")
    delivery = FakeDriveDelivery(expected)
    files = [RenderedFile(format=OutputFormat.CSV, filename="a.csv", local_path=Path("a.csv"))]

    result = upload_ask_files_to_drive(delivery, files, "cuantos usuarios hay")

    assert result is expected
    assert delivery.last_call["client_id"] == "preguntas_libres"
    assert delivery.last_call["files"] == files
