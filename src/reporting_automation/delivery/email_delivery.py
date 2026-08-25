from __future__ import annotations

import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from reporting_automation.config.models import DeliveryChannel, ReportConfig
from reporting_automation.delivery.base import DeliveryResult
from reporting_automation.rendering.base import RenderedFile
from reporting_automation.secrets.secret_manager import SecretManagerClient

# Puerto convencional de SMTP con TLS implicito (ej. relays de Gmail/muchos
# proveedores corporativos) -- ahi hay que conectar directo por SSL, no
# plaintext-y-luego-STARTTLS como en el resto de los puertos (587/25).
_SMTP_SSL_PORT = 465

# Sin esto, conectar a un puerto que espera un protocolo distinto al que
# asumimos (ej. TLS implicito en 465 conectado como si fuera STARTTLS)
# cuelga la conexion para siempre -- smtplib no tiene timeout por defecto.
_SMTP_TIMEOUT_SECONDS = 30


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

            # Todo lo que sigue -- armar el mensaje, adjuntar archivos, y
            # mandarlo por SMTP -- va en el mismo try/except: un secret mal
            # formado (falta from_address) o un archivo renderizado
            # inaccesible no debe escapar sin controlar, porque eso
            # tumbaria dispatch_delivery entero y ni siquiera se intentaria
            # el resto de los canales de entrega del reporte.
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

            host, port = smtp_config["host"], int(smtp_config["port"])
            server_cls = smtplib.SMTP_SSL if port == _SMTP_SSL_PORT else smtplib.SMTP
            with server_cls(host, port, timeout=_SMTP_TIMEOUT_SECONDS) as server:
                if port != _SMTP_SSL_PORT:
                    server.starttls()
                server.login(smtp_config["user"], smtp_config["password"])
                server.send_message(message)
        except Exception as exc:  # noqa: BLE001 - se reporta, no se traga
            return DeliveryResult(channel=DeliveryChannel.EMAIL, status="failed", detail=str(exc))

        return DeliveryResult(
            channel=DeliveryChannel.EMAIL, status="sent", detail=f"enviado a {', '.join(recipients)}"
        )
