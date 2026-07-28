from __future__ import annotations

from datetime import date

from googleapiclient.http import MediaFileUpload

from reporting_automation.config.models import DeliveryChannel, ReportConfig
from reporting_automation.delivery.base import DeliveryResult
from reporting_automation.rendering.base import RenderedFile


def _escape_drive_query(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("'", "\\'")


class GDriveDelivery:
    """Sube cada archivo generado a `<root>/<client_id>/<year><mes>/`.

    Porta tal cual la logica ya probada en el notebook original: busca
    carpeta/archivo por nombre, crea si no existe, actualiza si ya existe
    (para no duplicar si el reporte se vuelve a correr el mismo mes).
    """

    def __init__(
        self, drive_service, root_folder_id: str | None, run_date: date | None = None
    ) -> None:
        self._drive = drive_service
        self._root_folder_id = root_folder_id
        self._run_date = run_date

    def send(
        self,
        files: list[RenderedFile],
        report: ReportConfig,
        client_id: str,
        recipients: list[str],
    ) -> DeliveryResult:
        if not self._root_folder_id:
            return DeliveryResult(
                channel=DeliveryChannel.GDRIVE,
                status="failed",
                detail="falta gdrive_root_folder_id en settings",
            )

        try:
            run_date = self._run_date or date.today()
            client_folder_id, _ = self._get_or_create_folder(client_id, self._root_folder_id)
            month_folder_name = f"{run_date.year}{run_date.month:02d}"
            month_folder_id, _ = self._get_or_create_folder(month_folder_name, client_folder_id)

            links = [self._upload_or_update_file(rendered, month_folder_id) for rendered in files]
        except Exception as exc:  # noqa: BLE001 - se reporta, no se traga
            return DeliveryResult(channel=DeliveryChannel.GDRIVE, status="failed", detail=str(exc))

        return DeliveryResult(channel=DeliveryChannel.GDRIVE, status="sent", detail=", ".join(links))

    def _get_or_create_folder(self, name: str, parent_id: str) -> tuple[str, bool]:
        safe_name = _escape_drive_query(name)
        query = (
            f"'{parent_id}' in parents and name = '{safe_name}' "
            "and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        )
        result = self._drive.files().list(q=query, fields="files(id, name)").execute()
        existing = result.get("files", [])
        if existing:
            return existing[0]["id"], True

        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        created = self._drive.files().create(body=metadata, fields="id").execute()
        return created["id"], False

    def _find_file(self, filename: str, folder_id: str) -> dict | None:
        safe_name = _escape_drive_query(filename)
        query = f"'{folder_id}' in parents and name = '{safe_name}' and trashed = false"
        result = self._drive.files().list(q=query, fields="files(id, name, webViewLink)").execute()
        matches = result.get("files", [])
        return matches[0] if matches else None

    def _upload_or_update_file(self, rendered: RenderedFile, folder_id: str) -> str:
        existing = self._find_file(rendered.filename, folder_id)
        media = MediaFileUpload(str(rendered.local_path), resumable=True)

        if existing:
            updated = (
                self._drive.files()
                .update(fileId=existing["id"], media_body=media, fields="webViewLink")
                .execute()
            )
            return updated.get("webViewLink", "")

        metadata = {"name": rendered.filename, "parents": [folder_id]}
        created = (
            self._drive.files()
            .create(body=metadata, media_body=media, fields="webViewLink")
            .execute()
        )
        return created.get("webViewLink", "")
