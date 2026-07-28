from datetime import date

from reporting_automation.config.models import OutputFormat
from reporting_automation.gcs_landing import upload_rendered_files
from reporting_automation.rendering.base import RenderedFile


class FakeBlob:
    def __init__(self, name: str, sink: list):
        self.name = name
        self._sink = sink

    def upload_from_filename(self, path: str) -> None:
        self._sink.append((self.name, path))


class FakeBucket:
    def __init__(self, sink: list):
        self._sink = sink

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(name, self._sink)


class FakeGcsClient:
    def __init__(self):
        self.uploads: list[tuple[str, str]] = []

    def bucket(self, bucket_name: str) -> FakeBucket:
        self.bucket_name = bucket_name
        return FakeBucket(self.uploads)


def test_upload_rendered_files_builds_expected_paths(tmp_path):
    local_path = tmp_path / "202607_MD_Reporte.csv"
    local_path.write_text("a,b\n1,2\n")
    rendered = [RenderedFile(format=OutputFormat.CSV, filename=local_path.name, local_path=local_path)]

    client = FakeGcsClient()
    uris = upload_rendered_files(client, "mi-bucket", "protec", rendered, run_date=date(2026, 7, 1))

    assert uris == ["gs://mi-bucket/protec/2026/07/202607_MD_Reporte.csv"]
    assert client.uploads == [("protec/2026/07/202607_MD_Reporte.csv", str(local_path))]


def test_upload_rendered_files_handles_multiple_files(tmp_path):
    csv_path = tmp_path / "r.csv"
    xlsx_path = tmp_path / "r.xlsx"
    csv_path.write_text("a\n1\n")
    xlsx_path.write_bytes(b"fake")

    rendered = [
        RenderedFile(format=OutputFormat.CSV, filename="r.csv", local_path=csv_path),
        RenderedFile(format=OutputFormat.XLSX, filename="r.xlsx", local_path=xlsx_path),
    ]

    client = FakeGcsClient()
    uris = upload_rendered_files(client, "b", "runa", rendered, run_date=date(2026, 1, 15))

    assert uris == ["gs://b/runa/2026/01/r.csv", "gs://b/runa/2026/01/r.xlsx"]


def test_upload_rendered_files_empty_list_returns_empty(tmp_path):
    client = FakeGcsClient()
    assert upload_rendered_files(client, "b", "cliente", []) == []
