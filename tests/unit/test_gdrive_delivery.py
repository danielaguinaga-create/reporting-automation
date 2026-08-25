from datetime import date

from reporting_automation.config.models import OutputFormat, ReportConfig, ReportKind
from reporting_automation.delivery.gdrive_delivery import GDriveDelivery
from reporting_automation.rendering.base import RenderedFile


class FakeExecute:
    def __init__(self, result: dict):
        self._result = result

    def execute(self) -> dict:
        return self._result


class FakeFilesResource:
    def __init__(self, existing_folders: dict | None = None, existing_files: dict | None = None):
        self.existing_folders = existing_folders or {}
        self.existing_files = existing_files or {}
        self.created_folders: list[dict] = []
        self.created_files: list[dict] = []
        self.updated_files: list[tuple] = []

    def list(self, q: str, fields: str, orderBy: str | None = None):
        if "mimeType = 'application/vnd.google-apps.folder'" in q:
            for name, folder_id in self.existing_folders.items():
                if f"name = '{name}'" in q:
                    return FakeExecute({"files": [{"id": folder_id, "name": name}]})
            return FakeExecute({"files": []})

        for filename, meta in self.existing_files.items():
            if f"name = '{filename}'" in q:
                return FakeExecute({"files": [meta]})
        return FakeExecute({"files": []})

    def create(self, body: dict, fields: str, media_body=None):
        if media_body is not None:
            self.created_files.append(body)
            return FakeExecute({"id": "new-file-id", "webViewLink": f"https://drive/{body['name']}"})
        self.created_folders.append(body)
        return FakeExecute({"id": f"folder-{body['name']}"})

    def update(self, fileId: str, media_body, fields: str):
        self.updated_files.append((fileId, media_body))
        return FakeExecute({"webViewLink": f"https://drive/updated/{fileId}"})


class FakeDriveService:
    def __init__(self, files_resource: FakeFilesResource):
        self._files_resource = files_resource

    def files(self) -> FakeFilesResource:
        return self._files_resource


def _report(**overrides) -> ReportConfig:
    defaults = dict(
        id="r1",
        name="ReporteTest",
        kind=ReportKind.CUSTOM,
        client_id="acme",
        sql_file="r1.sql",
        output_formats=[OutputFormat.CSV],
    )
    defaults.update(overrides)
    return ReportConfig(**defaults)


def _rendered_file(tmp_path, filename="r.csv") -> RenderedFile:
    path = tmp_path / filename
    path.write_text("a,b\n1,2\n")
    return RenderedFile(format=OutputFormat.CSV, filename=filename, local_path=path)


def test_send_without_root_folder_id_fails_without_touching_drive():
    delivery = GDriveDelivery(drive_service=None, root_folder_id=None)
    result = delivery.send([], _report(), "acme", [])

    assert result.status == "failed"
    assert "gdrive_root_folder_id" in result.detail


def test_send_creates_folders_and_file_when_none_exist(tmp_path):
    files_resource = FakeFilesResource()
    drive_service = FakeDriveService(files_resource)
    delivery = GDriveDelivery(drive_service, root_folder_id="root123", run_date=date(2026, 7, 1))

    rendered = [_rendered_file(tmp_path)]
    result = delivery.send(rendered, _report(), "acme", [])

    assert result.status == "sent"
    assert len(files_resource.created_folders) == 2  # carpeta cliente + carpeta mes
    assert len(files_resource.created_files) == 1
    assert files_resource.created_files[0]["name"] == "r.csv"
    assert files_resource.updated_files == []


def test_send_updates_existing_file_instead_of_duplicating(tmp_path):
    files_resource = FakeFilesResource(
        existing_folders={"acme": "client-folder-id", "202607": "month-folder-id"},
        existing_files={"r.csv": {"id": "existing-id", "name": "r.csv", "webViewLink": "https://existing"}},
    )
    drive_service = FakeDriveService(files_resource)
    delivery = GDriveDelivery(drive_service, root_folder_id="root123", run_date=date(2026, 7, 1))

    rendered = [_rendered_file(tmp_path)]
    result = delivery.send(rendered, _report(), "acme", [])

    assert result.status == "sent"
    assert files_resource.created_folders == []
    assert files_resource.created_files == []
    assert len(files_resource.updated_files) == 1
    assert files_resource.updated_files[0][0] == "existing-id"


def test_get_or_create_folder_requests_created_time_ordering():
    """Si una carrera (reintento de Pub/Sub superpuesto) ya creo carpetas
    duplicadas con el mismo nombre, todas las llamadas deben converger en
    la misma (la mas vieja) en vez de en cualquiera al azar -- pedirle a la
    API que ordene por createdTime es lo que permite eso (ver hallazgo del
    code review sobre la carrera de list-then-create)."""
    captured_kwargs = {}

    class RecordingFilesResource(FakeFilesResource):
        def list(self, q, fields, orderBy=None):
            captured_kwargs["orderBy"] = orderBy
            return super().list(q, fields, orderBy)

    drive_service = FakeDriveService(RecordingFilesResource())
    delivery = GDriveDelivery(drive_service, root_folder_id="root123", run_date=date(2026, 7, 1))

    delivery._get_or_create_folder("acme", "root123")

    assert captured_kwargs["orderBy"] == "createdTime"


def test_send_drive_api_error_returns_failed_not_exception(tmp_path):
    class BoomFilesResource(FakeFilesResource):
        def list(self, q, fields, orderBy=None):
            raise RuntimeError("Drive API error")

    drive_service = FakeDriveService(BoomFilesResource())
    delivery = GDriveDelivery(drive_service, root_folder_id="root123")

    result = delivery.send([_rendered_file(tmp_path)], _report(), "acme", [])

    assert result.status == "failed"
    assert "Drive API error" in result.detail
