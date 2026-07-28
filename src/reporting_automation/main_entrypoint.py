from __future__ import annotations

import base64
import json
import logging
import tempfile

import google.auth
from flask import Flask, request
from google.cloud import bigquery, secretmanager, storage
from googleapiclient.discovery import build as build_drive_service

from reporting_automation.config.client_registry import ClientRegistry
from reporting_automation.config.loader import load_settings
from reporting_automation.config.models import DeliveryChannel
from reporting_automation.config.registry import ReportRegistry
from reporting_automation.delivery.dispatch import dispatch_delivery, resolve_recipients
from reporting_automation.delivery.email_delivery import EmailDelivery
from reporting_automation.delivery.gdrive_delivery import GDriveDelivery
from reporting_automation.gcs_landing import upload_rendered_files
from reporting_automation.logging_setup import configure_logging
from reporting_automation.orchestrator import run_report
from reporting_automation.query.bigquery_client import BigQueryExecutor
from reporting_automation.secrets.secret_manager import SecretManagerClient

logger = logging.getLogger("reporting_automation.entrypoint")

app = Flask(__name__)


class InvalidPubSubEnvelope(ValueError):
    pass


def decode_pubsub_payload(envelope: dict | None) -> dict:
    """Decodifica el body de un push de Pub/Sub a un dict.

    Formato esperado (el que arma Cloud Scheduler con --message-body):
    {"message": {"data": "<base64 de {reporte,cliente,receptores,params}>"}}
    """
    if not envelope or "message" not in envelope or "data" not in envelope["message"]:
        raise InvalidPubSubEnvelope("falta message.data en el push de Pub/Sub")

    try:
        raw = base64.b64decode(envelope["message"]["data"]).decode("utf-8")
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidPubSubEnvelope(f"payload invalido: {exc}") from exc


@app.post("/")
def handle_push():
    envelope = request.get_json(silent=True)

    try:
        payload = decode_pubsub_payload(envelope)
    except InvalidPubSubEnvelope as exc:
        logger.error("Push de Pub/Sub invalido: %s", exc)
        return (f"Bad Request: {exc}", 400)

    report_id = payload.get("reporte")
    client_id = payload.get("cliente")
    params = payload.get("params") or {}
    receptores = payload.get("receptores") or []

    if not report_id or not client_id:
        logger.error("Payload sin 'reporte'/'cliente': %s", payload)
        return ("Bad Request: 'reporte' y 'cliente' son requeridos", 400)

    settings = load_settings()
    registry = ReportRegistry()
    registry.load(settings.reports_dir)
    client_registry = ClientRegistry()
    client_registry.load(settings.clients_dir)

    executor = BigQueryExecutor(bigquery.Client(project=settings.gcp_project))

    with tempfile.TemporaryDirectory() as tmp_dir:
        result = run_report(
            report_id=report_id,
            client_id=client_id,
            params=params,
            output_dir=tmp_dir,
            registry=registry,
            executor=executor,
            client_registry=client_registry,
        )

        if result.status == "failure":
            logger.error(
                "Fallo generando reporte",
                extra={"report_id": report_id, "client_id": client_id, "error": result.error},
            )
            # Pub/Sub reintenta sobre 5xx. No distinguimos aqui error
            # transitorio (ej. timeout de BigQuery) de permanente (ej.
            # reporte que ya no existe) -- ver plan, seccion "Abierto".
            return (f"Error ejecutando {report_id!r}: {result.error}", 500)

        try:
            storage_client = storage.Client(project=settings.gcp_project)
            gcs_uris = upload_rendered_files(
                storage_client, settings.trace_bucket, client_id, result.rendered_files
            )
        except Exception as exc:  # noqa: BLE001 - se reporta al caller, no se traga
            logger.error(
                "Reporte generado pero fallo la subida a GCS",
                extra={"report_id": report_id, "client_id": client_id, "error": str(exc)},
            )
            # Retryable: el bucket puede no existir todavia, o ser un fallo
            # transitorio de red/permisos. Pub/Sub reintenta sobre 5xx.
            return (f"Error subiendo a GCS ({report_id!r}, {client_id!r}): {exc}", 500)

    logger.info(
        "Reporte generado y subido a GCS",
        extra={
            "report_id": report_id,
            "client_id": client_id,
            "rows": result.rows,
            "gcs_uris": gcs_uris,
            "receptores": receptores,
        },
    )

    report = registry.get(report_id)
    if report.delivery_channels:
        # El archivo ya quedo en GCS (arriba) -- eso es lo que cuenta como
        # "generado" para efectos de reintentos de Pub/Sub. Un fallo de
        # entrega (correo/Drive) se loguea pero NO hace que este handler
        # devuelva 500, para no regenerar y volver a subir el reporte a GCS
        # solo porque el envio fallo.
        try:
            secret_manager = SecretManagerClient(
                secretmanager.SecretManagerServiceClient(), settings.gcp_project
            )
            credentials, _ = google.auth.default()
            drive_service = build_drive_service("drive", "v3", credentials=credentials)
            delivery_factories = {
                DeliveryChannel.EMAIL: EmailDelivery(secret_manager),
                DeliveryChannel.GDRIVE: GDriveDelivery(drive_service, settings.gdrive_root_folder_id),
            }
            recipients = resolve_recipients(report, receptores)
            delivery_results = dispatch_delivery(
                report, result.rendered_files, recipients, client_id, delivery_factories
            )
            for delivery_result in delivery_results:
                log_fn = logger.info if delivery_result.status == "sent" else logger.error
                log_fn(
                    "Resultado de entrega",
                    extra={
                        "report_id": report_id,
                        "client_id": client_id,
                        "channel": delivery_result.channel.value,
                        "status": delivery_result.status,
                        "detail": delivery_result.detail,
                    },
                )
        except Exception as exc:  # noqa: BLE001 - no debe tumbar la respuesta 200
            logger.error(
                "Fallo inesperado despachando delivery",
                extra={"report_id": report_id, "client_id": client_id, "error": str(exc)},
            )

    return ("OK", 200)


configure_logging()

if __name__ == "__main__":
    import os

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
