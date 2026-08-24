import base64
import json

import pandas as pd
import pytest

from reporting_automation import main_entrypoint
from reporting_automation.config.loader import Settings


class FakeQueryJob:
    def __init__(self, df: pd.DataFrame):
        self._df = df

    def to_dataframe(self, create_bqstorage_client: bool = True) -> pd.DataFrame:
        return self._df


class FakeBigQueryClient:
    def __init__(self, project: str | None = None):
        self._df = pd.DataFrame({"dummy_col": [1, 2, 3]})

    def query(self, sql, job_config=None):
        return FakeQueryJob(self._df)


class FakeBlob:
    def __init__(self, name, sink):
        self.name = name
        self._sink = sink

    def upload_from_filename(self, path: str) -> None:
        self._sink.append((self.name, path))


class FakeBucket:
    def __init__(self, sink):
        self._sink = sink

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(name, self._sink)


class FakeStorageClient:
    uploads: list = []

    def __init__(self, project: str | None = None):
        pass

    def bucket(self, bucket_name: str) -> FakeBucket:
        return FakeBucket(FakeStorageClient.uploads)


def _pubsub_envelope(payload: dict) -> dict:
    data = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return {"message": {"data": data}}


@pytest.fixture(autouse=True)
def _patch_gcp_clients(monkeypatch, reports_fixtures_dir, clients_fixtures_dir):
    FakeStorageClient.uploads = []
    monkeypatch.setattr(main_entrypoint.bigquery, "Client", FakeBigQueryClient)
    monkeypatch.setattr(main_entrypoint.storage, "Client", FakeStorageClient)
    monkeypatch.setattr(
        main_entrypoint,
        "load_settings",
        lambda *a, **k: Settings(
            gcp_project="test-project",
            bigquery_dataset="test_dataset",
            reports_dir=str(reports_fixtures_dir),
            clients_dir=str(clients_fixtures_dir),
            trace_bucket="test-bucket",
        ),
    )


@pytest.fixture
def client():
    main_entrypoint.app.config.update(TESTING=True)
    return main_entrypoint.app.test_client()


def test_handle_push_valid_payload_returns_200(client):
    envelope = _pubsub_envelope(
        {"reporte": "simple_report", "cliente": "acme", "receptores": ["x@y.com"], "params": {}}
    )

    response = client.post("/", json=envelope)

    assert response.status_code == 200
    assert len(FakeStorageClient.uploads) == 1
    assert FakeStorageClient.uploads[0][0].startswith("acme/")


def test_handle_push_resolves_window_preset(client):
    """Un reporte programado con ventana de tiempo (start_date/end_date en su
    params_schema) debe poder llegar via Pub/Sub con solo el preset -- antes
    el payload no cargaba 'window' y el entrypoint no lo pasaba a run_report,
    asi que esto fallaba con un parametro faltante."""
    envelope = _pubsub_envelope(
        {"reporte": "windowed_report", "cliente": "acme", "params": {}, "window": "last_7_days"}
    )

    response = client.post("/", json=envelope)

    assert response.status_code == 200
    assert len(FakeStorageClient.uploads) == 1


def test_handle_push_missing_report_or_client_returns_400(client):
    envelope = _pubsub_envelope({"cliente": "acme"})
    response = client.post("/", json=envelope)
    assert response.status_code == 400


def test_handle_push_invalid_envelope_returns_400(client):
    response = client.post("/", json={"not": "a valid pubsub envelope"})
    assert response.status_code == 400


def test_handle_push_no_json_body_returns_400(client):
    response = client.post("/", data="not json", content_type="text/plain")
    assert response.status_code == 400


def test_handle_push_unknown_report_returns_500(client):
    envelope = _pubsub_envelope({"reporte": "does_not_exist", "cliente": "acme", "params": {}})
    response = client.post("/", json=envelope)
    assert response.status_code == 500
    assert FakeStorageClient.uploads == []


def test_handle_push_gcs_upload_failure_returns_500_not_a_crash(client, monkeypatch):
    """Si el bucket no existe (o falla la subida), debe responder 500 con
    mensaje claro, no propagar la excepcion sin controlar."""

    def _boom(bucket_name: str):
        raise RuntimeError("The specified bucket does not exist.")

    monkeypatch.setattr(FakeStorageClient, "bucket", lambda self, bucket_name: _boom(bucket_name))

    envelope = _pubsub_envelope({"reporte": "simple_report", "cliente": "acme", "params": {}})
    response = client.post("/", json=envelope)

    assert response.status_code == 500
    assert "GCS" in response.get_data(as_text=True)
