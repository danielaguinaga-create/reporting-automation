from __future__ import annotations

from datetime import date
from typing import Protocol

from google.cloud import storage

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


def try_land_rendered_files(
    client: GcsClient,
    bucket_name: str | None,
    client_id: str,
    rendered_files: list[RenderedFile],
    run_date: date | None = None,
) -> tuple[list[str], str | None]:
    """Como `upload_rendered_files`, pero nunca levanta.

    Pensada para las corridas interactivas (CLI/UI): si `bucket_name` no esta
    configurado, o el bucket todavia no existe, o faltan permisos, la corrida
    ya genero el archivo local con exito -- este paso es una copia de
    auditoria adicional, no debe hacer fallar el reporte. Devuelve
    `(uris, None)` si subio bien, o `([], mensaje_de_error)` si no.
    """
    if not bucket_name:
        return [], None
    try:
        uris = upload_rendered_files(client, bucket_name, client_id, rendered_files, run_date)
    except Exception as exc:  # noqa: BLE001 - se reporta, no debe tumbar la corrida
        return [], str(exc)
    return uris, None


def try_land_rendered_files_for_project(
    project: str,
    bucket_name: str | None,
    client_id: str,
    rendered_files: list[RenderedFile],
    run_date: date | None = None,
) -> tuple[list[str], str | None]:
    """Como `try_land_rendered_files`, pero tambien construye el `storage.Client`.

    `storage.Client(...)` resuelve credenciales al construirse, no solo al
    subir -- si eso falla (ej. credenciales invalidas en ese entorno), antes
    ese error escapaba sin capturar porque el cliente se creaba afuera de
    cualquier try/except, tumbando la corrida despues de que el reporte ya
    se genero con exito. Centralizar la construccion aca evita repetir el
    mismo try/except en cada llamador (CLI `run`/`run-batch`, UI).
    """
    if not bucket_name:
        return [], None
    try:
        client = storage.Client(project=project)
    except Exception as exc:  # noqa: BLE001 - misma politica que try_land_rendered_files
        return [], str(exc)
    return try_land_rendered_files(client, bucket_name, client_id, rendered_files, run_date)
