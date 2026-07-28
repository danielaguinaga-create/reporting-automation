from __future__ import annotations

from datetime import date
from typing import Protocol

from reporting_automation.rendering.base import RenderedFile


class GcsBucket(Protocol):
    """Subconjunto de `google.cloud.storage.Bucket` que este modulo necesita."""

    def blob(self, name: str): ...  # noqa: ANN401 - devuelve google.cloud.storage.Blob


class GcsClient(Protocol):
    """Subconjunto de `google.cloud.storage.Client` que este modulo necesita."""

    def bucket(self, bucket_name: str) -> GcsBucket: ...


def upload_rendered_files(
    client: GcsClient,
    bucket_name: str,
    client_id: str,
    rendered_files: list[RenderedFile],
    run_date: date | None = None,
) -> list[str]:
    """Sube cada archivo generado a `gs://{bucket}/{client_id}/{year}/{month}/{filename}`.

    Sirve de entrega interina (alguien lo baja manualmente del bucket) y de
    registro de auditoria, mientras no exista delivery automatico real
    (correo/FTP/GDrive, Fase 2). Devuelve las URIs `gs://` resultantes.
    """
    run_date = run_date or date.today()
    bucket = client.bucket(bucket_name)

    uris = []
    for rendered in rendered_files:
        blob_name = f"{client_id}/{run_date.year}/{run_date.month:02d}/{rendered.filename}"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(rendered.local_path))
        uris.append(f"gs://{bucket_name}/{blob_name}")

    return uris
