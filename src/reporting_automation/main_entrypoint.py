from __future__ import annotations

import base64
import json
import logging
import tempfile
from datetime import date

from flask import Flask, request
from google.cloud import bigquery, storage
from pydantic import ValidationError

from reporting_automation.batch import BatchEntry
from reporting_automation.config.client_registry import ClientRegistry
from reporting_automation.config.loader import load_settings
from reporting_automation.config.registry import ReportRegistry
from reporting_automation.delivery.dispatch import dispatch_delivery, resolve_recipients
from reporting_automation.delivery.factory import build_delivery_factories
from reporting_automation.gcs_landing import upload_rendered_files
from reporting_automation.logging_setup import configure_logging
from reporting_automation.orchestrator import run_report
from reporting_automation.query.bigquery_client import BigQueryExecutor

logger = logging.getLogger("reporting_automation.entrypoint")

app = Flask(__name__)

_state: dict | None = None


def _get_state() -> dict:
    """Construye settings/registries/cliente de BigQuery/delivery factories
    una sola vez por proceso (cold start de Cloud Run), no en cada push --
    releer/reparsear config/reports/ y config/clients/ (y reautenticar un
    bigquery.Client) en cada request desperdicia trabajo que Cloud Run
    mantiene vivo entre requests mientras la instancia siga caliente.

    Perezoso (no se construye al importar el modulo) para que los tests
    puedan mockear `bigquery.Client`/`storage.Client`/`load_settings` antes
    de la primera request -- si esto se construyera al importar, los tests
    tendrian que mockear antes del `import main_entrypoint`, algo que
    pytest no permite hacer de forma limpia.
    """
    global _state
    if _state is None:
        settings = load_settings()
        registry = ReportRegistry()
        registry.load(settings.reports_dir)
        client_registry = ClientRegistry()
        client_registry.load(settings.clients_dir)
        _state = {
            "settings": settings,
            "registry": registry,
            "client_registry": client_registry,
            "executor": BigQueryExecutor(bigquery.Client(project=settings.gcp_project)),
            "delivery_factories": build_delivery_factories(settings),
        }
    return _state


class InvalidPubSubEnvelope(ValueError):
    pass


def decode_pubsub_payload(envelope: dict | None) -> dict:
    """Decodifica el body de un push de Pub/Sub a un dict.

    Formato esperado (el que arma Cloud Scheduler con --message-body):
    {"message": {"data": "<base64 de {reporte,cliente,receptores,params,window}>"}}
    """
    message = envelope.get("message") if envelope else None
    if not isinstance(message, dict) or "data" not in message:
        raise InvalidPubSubEnvelope("falta message.data en el push de Pub/Sub")

    try:
        raw = base64.b64decode(message["data"]).decode("utf-8")
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidPubSubEnvelope(f"payload invalido: {exc}") from exc


def _delivery_marker_blob_name(report_id: str, client_id: str, year: int, month: int) -> str:
    return f"{client_id}/{year}/{month:02d}/.delivered_{report_id}"


@app.post("/")
def handle_push():
    state = _get_state()
    settings = state["settings"]
    registry = state["registry"]
    client_registry = state["client_registry"]
    executor = state["executor"]
    delivery_factories = state["delivery_factories"]

    envelope = request.get_json(silent=True)

    try:
        payload = decode_pubsub_payload(envelope)
    except InvalidPubSubEnvelope as exc:
        logger.error("Push de Pub/Sub invalido: %s", exc)
        return (f"Bad Request: {exc}", 400)

    report_id = payload.get("reporte")
    client_id = payload.get("cliente")

    if not report_id or not client_id:
        logger.error("Payload sin 'reporte'/'cliente': %s", payload)
        return ("Bad Request: 'reporte' y 'cliente' son requeridos", 400)

    # Se construye el mismo BatchEntry que arma run-batch (CLI) para que la
    # traduccion a los kwargs de run_report (via `run_report_kwargs()`) sea
    # una sola, compartida entre los dos caminos que ejecutan un reporte
    # programado -- ver batch.BatchEntry.run_report_kwargs. Payload malformado
    # (tipos que no matchean, ej. `window` numerico) se rechaza con 400 en vez
    # de tumbar el handler con una excepcion sin capturar -- eso ultimo
    # convertiria un mensaje malformado en un reintento infinito de Pub/Sub,
    # ya que el payload nunca cambia entre reintentos.
    try:
        entry = BatchEntry(
            report=report_id,
            client=client_id,
            params=payload.get("params") or {},
            recipients=payload.get("receptores") or [],
            window=payload.get("window"),
        )
    except ValidationError as exc:
        logger.error("Payload de Pub/Sub invalido: %s", exc)
        return (f"Bad Request: payload invalido: {exc}", 400)

    try:
        report = registry.get(report_id)
    except Exception as exc:  # noqa: BLE001 - reporte inexistente, no es transitorio
        logger.error(
            "Reporte no encontrado", extra={"report_id": report_id, "error": str(exc)}
        )
        return (f"Bad Request: {exc}", 400)

    with tempfile.TemporaryDirectory() as tmp_dir:
        result = run_report(
            **entry.run_report_kwargs(),
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
                "receptores": entry.recipients,
            },
        )

        # La entrega tiene que pasar ANTES de que el `with` cierre y borre
        # tmp_dir: EmailDelivery/GDriveDelivery leen `rendered.local_path`
        # del disco (adjuntar el mail, subir a Drive), no bytes ya en
        # memoria -- si esto corriera despues del `with`, el archivo ya no
        # existiria y la entrega fallaria siempre, en silencio (se atrapa
        # como excepcion generica abajo y el handler sigue devolviendo 200).
        if report.delivery_channels:
            run_date = date.today()
            marker_name = _delivery_marker_blob_name(
                report_id, client_id, run_date.year, run_date.month
            )
            marker_blob = storage_client.bucket(settings.trace_bucket).blob(marker_name)
            try:
                already_delivered = marker_blob.exists()
            except Exception as exc:  # noqa: BLE001 - si falla el check, mejor intentar entregar
                logger.error(
                    "No se pudo chequear el marcador de entrega, se intenta entregar igual",
                    extra={"report_id": report_id, "client_id": client_id, "error": str(exc)},
                )
                already_delivered = False

            if already_delivered:
                # Pub/Sub es at-least-once: un push puede reentregarse (ack
                # perdido, reintento, autoescalado) aunque ya haya corrido
                # con exito. Sin este marcador, un reintento manda el mismo
                # correo dos veces -- a diferencia de la subida a GCS
                # (pisar el mismo archivo es inofensivo), un mail duplicado
                # si es un problema real.
                logger.info(
                    "Entrega ya despachada para este periodo, se omite (dedup de reintento)",
                    extra={"report_id": report_id, "client_id": client_id},
                )
            else:
                # El archivo ya quedo en GCS (arriba) -- eso es lo que cuenta
                # como "generado" para efectos de reintentos de Pub/Sub. Un
                # fallo de entrega (correo/Drive) se loguea pero NO hace que
                # este handler devuelva 500, para no regenerar y volver a
                # subir el reporte a GCS solo porque el envio fallo.
                try:
                    recipients = resolve_recipients(report, entry.recipients)
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
                    marker_blob.upload_from_string("")
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
