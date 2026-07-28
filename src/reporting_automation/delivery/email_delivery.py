from __future__ import annotations

import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from reporting_automation.config.models import DeliveryChannel, ReportConfig
from reporting_automation.delivery.base import DeliveryResult
from reporting_automation.rendering.base import RenderedFile
from reporting_automation.secrets.secret_manager import SecretManagerClient


class EmailDelivery:
    """Envia los archivos generados como adjuntos por SMTP.

    Las credenciales (host, port, user, password, from_address) se leen de
    Secret Manager -- convencion: secret `internal-smtp` con esos campos en
    JSON (ver README, Fase 2). Se leen de forma perezosa (solo dentro de
    `send`), asi que construir el objeto no requiere red ni el secret
    ya creado.
    """

    def __init__(self, secret_manager: SecretManagerClient, secret_id: str = "internal-smtp") -> None:
        self._secret_manager = secret_manager
        self._secret_id = secret_id

    def send(
        self,
        files: list[RenderedFile],
        report: ReportConfig,
        client_id: str,
        recipients: list[str],
    ) -> DeliveryResult:
        if not recipients:
            return DeliveryResult(
                channel=DeliveryChannel.EMAIL, status="failed", detail="sin destinatarios"
            )

        try:
            smtp_config = self._secret_manager.get_json_secret(self._secret_id)
        except Exception as exc:  # noqa: BLE001 - se reporta, no se traga
            return DeliveryResult(
                channel=DeliveryChannel.EMAIL,
                status="failed",
                detail=f"no se pudo leer el secret {self._secret_id!r}: {exc}",
            )

        message = MIMEMultipart()
        message["From"] = smtp_config["from_address"]
        message["To"] = ", ".join(recipients)
        message["Subject"] = f"{report.name} - {client_id}"
        message.attach(MIMEText(f"Adjunto {report.name} para {client_id}.", "plain"))

        for rendered in files:
            with open(rendered.local_path, "rb") as f:
                attachment = MIMEApplication(f.read(), Name=rendered.filename)
            attachment["Content-Disposition"] = f'attachment; filename="{rendered.filename}"'
            message.attach(attachment)

        try:
            with smtplib.SMTP(smtp_config["host"], int(smtp_config["port"])) as server:
                server.starttls()
                server.login(smtp_config["user"], smtp_config["password"])
                server.send_message(message)
        except Exception as exc:  # noqa: BLE001 - se reporta, no se traga
            return DeliveryResult(channel=DeliveryChannel.EMAIL, status="failed", detail=str(exc))

        return DeliveryResult(
            channel=DeliveryChannel.EMAIL, status="sent", detail=f"enviado a {', '.join(recipients)}"
        )
