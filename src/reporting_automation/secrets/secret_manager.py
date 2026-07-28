from __future__ import annotations

import json
from typing import Any, Protocol


class SecretVersionResponse(Protocol):
    payload: Any  # tiene .data: bytes


class SecretManagerServiceClient(Protocol):
    """Subconjunto de `google.cloud.secretmanager.SecretManagerServiceClient`
    que este modulo necesita."""

    def access_secret_version(self, request: dict) -> SecretVersionResponse: ...


class SecretManagerClient:
    """Envoltorio delgado sobre Secret Manager.

    Una sola convencion de nombres por ahora (ver README, Fase 2):
    `internal-smtp` guarda un JSON con las credenciales de envio de correo.
    No hay secretos por cliente todavia -- GDrive usa las credenciales ADC
    ya en uso para BigQuery/GCS, sin secret propio.
    """

    def __init__(self, client: SecretManagerServiceClient, project: str) -> None:
        self._client = client
        self._project = project

    def get_secret(self, secret_id: str, version: str = "latest") -> str:
        name = f"projects/{self._project}/secrets/{secret_id}/versions/{version}"
        response = self._client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8")

    def get_json_secret(self, secret_id: str, version: str = "latest") -> dict:
        return json.loads(self.get_secret(secret_id, version))
