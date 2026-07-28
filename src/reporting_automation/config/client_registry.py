from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from reporting_automation.config.models import ClientConfig
from reporting_automation.exceptions import ClientConfigError


class ClientRegistry:
    """Descubre clientes definidos como `<id>.yaml` bajo `clients_dir`.

    A diferencia de `ReportRegistry`, un cliente sin config registrada es
    valido (`get_or_none` devuelve None): `--client` en la CLI siempre
    funciono como una etiqueta libre, esto solo la enriquece cuando existe
    config para ese id.
    """

    def __init__(self) -> None:
        self._clients: dict[str, ClientConfig] = {}

    def load(self, clients_dir: str | Path) -> None:
        clients_dir = Path(clients_dir)
        if not clients_dir.is_dir():
            return

        for yaml_path in sorted(clients_dir.glob("*.yaml")):
            with yaml_path.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)

            try:
                client = ClientConfig.model_validate(raw)
            except ValidationError as exc:
                raise ClientConfigError(f"Config invalida en {yaml_path}: {exc}") from exc

            if client.id in self._clients:
                raise ClientConfigError(f"Client id duplicado: {client.id!r} ({yaml_path})")

            self._clients[client.id] = client

    def get_or_none(self, client_id: str) -> ClientConfig | None:
        return self._clients.get(client_id)

    def list_all(self) -> list[ClientConfig]:
        return list(self._clients.values())
