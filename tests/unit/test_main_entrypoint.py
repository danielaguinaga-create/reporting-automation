import base64
import json
from pathlib import Path

import pandas as pd
import pytest

from reporting_automation import main_entrypoint
from reporting_automation.config.loader import Settings
from reporting_automation.config.models import DeliveryChannel
from reporting_automation.config.registry import ReportRegistry


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
    def __init__(self, name, sink, markers):
        self.name = name
        self._sink = sink
        self._markers = markers

    def upload_from_filename(self, path: str) -> None:
        self._sink.append((self.name, path))
        # Confirma que el archivo local todavia existe en este punto -- el
        # bug que este fixture ayuda a detectar borraba tmp_dir antes de
        # que la entrega (que corre despues de la subida a GCS) pudiera
        # leerlo.
        assert Path(path).exists(), f"{path} no existe en el momento de subir a GCS"

    def exists(self) -> bool:
        return self.name in self._markers

    def upload_from_string(self, data: str) -> None:
        self._markers.add(self.name)


class FakeBucket:
    def __init__(self, sink, markers):
        self._sink = sink
        self._markers = markers

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(name, self._sink, self._markers)


class FakeStorageClient:
    uploads: list = []
    markers: set = set()

    def __init__(self, project: str | None = None):
        pass

    def bucket(self, bucket_name: str) -> FakeBucket:
        return FakeBucket(FakeStorageClient.uploads, FakeStorageClient.markers)


def _pubsub_envelope(payload: dict) -> dict:
    data = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return {"message": {"data": data}}


@pytest.fixture(autouse=True)
def _patch_gcp_clients(monkeypatch, reports_fixtures_dir, clients_fixtures_dir):
    # _state se cachea perezosamente en el primer request (ver
    # main_entrypoint._get_state) -- sin resetearlo, el primer test que
    # corra en el proceso construiria el estado real UNA vez, y todos los
    # tests siguientes reutilizarian ese mismo estado cacheado en vez del
    # que cada test necesita mockear.
    monkeypatch.setattr(main_entrypoint, "_state", None)
    FakeStorageClient.uploads = []
    FakeStorageClient.markers = set()
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
    monkeypatch.setattr("google.auth.default", lambda *a, **k: (None, "test-project"))
    monkeypatch.setattr(
        "reporting_automation.delivery.factory.build_drive_service",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "reporting_automation.delivery.factory.secretmanager.SecretManagerServiceClient",
        lambda *a, **k: None,
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


def test_handle_push_unknown_report_returns_400_not_500(client):
    """Un report_id que no existe nunca va a empezar a existir solo con
    reintentos -- devolver 500 (como antes) hace que Pub/Sub reintente para
    siempre un mensaje que jamas va a tener éxito. 400 es correcto: error
    permanente del cliente, no transitorio."""
    envelope = _pubsub_envelope({"reporte": "does_not_exist", "cliente": "acme", "params": {}})
    response = client.post("/", json=envelope)
    assert response.status_code == 400
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


def _with_delivery_channels(monkeypatch, report_id: str, channels: list[DeliveryChannel]) -> None:
    original_get = ReportRegistry.get

    def patched_get(self, rid):
        report = original_get(self, rid)
        if rid == report_id:
            return report.model_copy(update={"delivery_channels": channels})
        return report

    monkeypatch.setattr(ReportRegistry, "get", patched_get)


def test_handle_push_dispatches_delivery_before_tmpdir_cleanup(client, monkeypatch):
    """Antes de este fix, dispatch_delivery corria despues de que el `with
    tempfile.TemporaryDirectory()` ya habia borrado los archivos generados
    -- la entrega fallaba siempre en silencio (200 igual, error solo en
    logs). Este test confirma que dispatch_delivery recibe rutas que
    TODAVIA existen en disco en el momento en que se llama."""
    _with_delivery_channels(monkeypatch, "simple_report", [DeliveryChannel.EMAIL])

    captured_paths = []

    def fake_dispatch_delivery(report, rendered_files, recipients, client_id, delivery_factories):
        captured_paths.extend(rf.local_path for rf in rendered_files)
        assert all(p.exists() for p in captured_paths), "dispatch_delivery corrio con archivos ya borrados"
        return []

    monkeypatch.setattr(main_entrypoint, "dispatch_delivery", fake_dispatch_delivery)

    envelope = _pubsub_envelope({"reporte": "simple_report", "cliente": "acme", "params": {}})
    response = client.post("/", json=envelope)

    assert response.status_code == 200
    assert len(captured_paths) == 1


def test_handle_push_malformed_window_type_returns_400_not_500(client):
    """window numerico en vez de string (payload malformado) debe rechazarse
    con 400 -- antes tumbaba BatchEntry(...) con un ValidationError sin
    capturar, produciendo un 500 que Pub/Sub reintenta para siempre sobre
    un payload que nunca va a dejar de ser invalido."""
    envelope = _pubsub_envelope(
        {"reporte": "simple_report", "cliente": "acme", "params": {}, "window": 30}
    )
    response = client.post("/", json=envelope)
    assert response.status_code == 400


def test_handle_push_null_message_returns_400_not_crash(client):
    """{"message": null} es JSON valido pero rompia el chequeo de forma del
    envelope con un TypeError sin capturar (`"data" not in None`) en vez del
    InvalidPubSubEnvelope esperado."""
    response = client.post("/", json={"message": None})
    assert response.status_code == 400


def test_handle_push_redelivery_does_not_dispatch_delivery_twice(client, monkeypatch):
    """Pub/Sub es at-least-once: el mismo mensaje puede reentregarse aunque
    ya se haya procesado con exito. Sin el marcador de entrega, un reintento
    manda el mismo correo dos veces."""
    _with_delivery_channels(monkeypatch, "simple_report", [DeliveryChannel.EMAIL])

    calls = []
    monkeypatch.setattr(
        main_entrypoint, "dispatch_delivery", lambda *a, **k: calls.append(1) or []
    )

    envelope = _pubsub_envelope({"reporte": "simple_report", "cliente": "acme", "params": {}})
    first = client.post("/", json=envelope)
    second = client.post("/", json=envelope)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(calls) == 1
