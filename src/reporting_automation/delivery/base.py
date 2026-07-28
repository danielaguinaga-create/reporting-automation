from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from reporting_automation.config.models import DeliveryChannel, ReportConfig
from reporting_automation.rendering.base import RenderedFile


@dataclass(frozen=True)
class DeliveryResult:
    channel: DeliveryChannel
    status: Literal["sent", "failed"]
    detail: str | None = None


class Delivery(Protocol):
    def send(
        self,
        files: list[RenderedFile],
        report: ReportConfig,
        client_id: str,
        recipients: list[str],
    ) -> DeliveryResult: ...
