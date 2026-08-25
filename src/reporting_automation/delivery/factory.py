from __future__ import annotations

import google.auth
from google.cloud import secretmanager
from googleapiclient.discovery import build as build_drive_service

from reporting_automation.config.loader import Settings
from reporting_automation.config.models import DeliveryChannel
from reporting_automation.delivery.base import Delivery
from reporting_automation.delivery.email_delivery import EmailDelivery
from reporting_automation.delivery.gdrive_delivery import GDriveDelivery
from reporting_automation.secrets.secret_manager import SecretManagerClient

_NOT_YET_IMPLEMENTED = {DeliveryChannel.FTP}


def build_delivery_factories(settings: Settings) -> dict[DeliveryChannel, Delivery]:
    """Construye los `Delivery` reales (SMTP via Secret Manager, GDrive via
    ADC) -- no toca red hasta que `.send()` se invoque de verdad.

    Un solo lugar para esto: antes vivia duplicado en `cli.py` y en
    `main_entrypoint.py`, con riesgo de que un cambio (nuevo canal, nuevo
    scope de Drive) se aplicara a uno y no al otro -- la misma clase de bug
    que ya paso una vez con `BatchEntry.window` (ver `batch.py`)."""
    secret_manager = SecretManagerClient(secretmanager.SecretManagerServiceClient(), settings.gcp_project)
    credentials, _ = google.auth.default()
    drive_service = build_drive_service("drive", "v3", credentials=credentials)
    return {
        DeliveryChannel.EMAIL: EmailDelivery(secret_manager),
        DeliveryChannel.GDRIVE: GDriveDelivery(drive_service, settings.gdrive_root_folder_id),
    }


def get_delivery(
    channel: DeliveryChannel, delivery_factories: dict[DeliveryChannel, Delivery]
) -> Delivery:
    if channel in _NOT_YET_IMPLEMENTED:
        raise NotImplementedError(
            f"Delivery para {channel.value!r} no esta implementado en Fase 2 "
            "(no hay un servidor FTP real contra el cual probarlo, ver README)."
        )
    try:
        return delivery_factories[channel]
    except KeyError:
        raise ValueError(f"Canal de entrega desconocido: {channel!r}") from None
