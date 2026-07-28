from __future__ import annotations

from reporting_automation.config.models import DeliveryChannel, ReportConfig
from reporting_automation.delivery.base import Delivery, DeliveryResult
from reporting_automation.delivery.factory import get_delivery
from reporting_automation.rendering.base import RenderedFile


def resolve_recipients(report: ReportConfig, override: list[str] | None) -> list[str]:
    """`override` (ej. `--recipients` en CLI, `receptores` del batch/Pub-Sub)
    gana sobre `report.default_recipients` si viene con algo."""
    if override:
        return override
    return report.default_recipients


def dispatch_delivery(
    report: ReportConfig,
    rendered_files: list[RenderedFile],
    recipients: list[str],
    client_id: str,
    delivery_factories: dict[DeliveryChannel, Delivery],
) -> list[DeliveryResult]:
    """Corre `report.delivery_channels` uno por uno. No toca `orchestrator.py`:
    se llama despues de un `run_report` exitoso (ver `cli.py`/`main_entrypoint.py`).

    Un canal sin implementacion (ej. `ftp`) devuelve un `DeliveryResult`
    fallido con mensaje claro, no interrumpe los demas canales.
    """
    results = []
    for channel in report.delivery_channels:
        try:
            delivery = get_delivery(channel, delivery_factories)
        except (NotImplementedError, ValueError) as exc:
            results.append(DeliveryResult(channel=channel, status="failed", detail=str(exc)))
            continue

        results.append(delivery.send(rendered_files, report, client_id, recipients))

    return results
