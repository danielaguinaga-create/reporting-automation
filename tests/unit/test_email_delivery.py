import reporting_automation.delivery.email_delivery as email_delivery_module
from reporting_automation.config.models import (
    DeliveryChannel,
    OutputFormat,
    ReportConfig,
    ReportKind,
)
from reporting_automation.delivery.email_delivery import EmailDelivery
from reporting_automation.rendering.base import RenderedFile

VALID_SECRET = {
    "host": "smtp.gmail.com",
    "port": 587,
    "user": "reportes@meetingdoctors.com",
    "password": "app-password",
    "from_address": "reportes@meetingdoctors.com",
}


class FakeSecretManager:
    def __init__(self, secret: dict | None = None, raise_exc: Exception | None = None):
        self._secret = secret
        self._raise = raise_exc

    def get_json_secret(self, secret_id: str, version: str = "latest") -> dict:
        if self._raise:
            raise self._raise
        return self._secret


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in = None
        self.sent_message = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        self.sent_message = message


def _report(**overrides) -> ReportConfig:
    defaults = dict(
        id="r1",
        name="ReporteTest",
        kind=ReportKind.CUSTOM,
        client_id="acme",
        sql_file="r1.sql",
        output_formats=[OutputFormat.CSV],
    )
    defaults.update(overrides)
    return ReportConfig(**defaults)


def test_send_without_recipients_fails_without_touching_secret_manager():
    delivery = EmailDelivery(FakeSecretManager())
    result = delivery.send([], _report(), "acme", [])

    assert result.status == "failed"
    assert result.channel == DeliveryChannel.EMAIL


def test_send_success_builds_and_sends_message(tmp_path, monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(email_delivery_module.smtplib, "SMTP", FakeSMTP)

    csv_path = tmp_path / "r.csv"
    csv_path.write_text("a,b\n1,2\n")
    files = [RenderedFile(format=OutputFormat.CSV, filename="r.csv", local_path=csv_path)]

    delivery = EmailDelivery(FakeSecretManager(VALID_SECRET))
    result = delivery.send(files, _report(), "acme", ["destino@cliente.com"])

    assert result.status == "sent"
    smtp = FakeSMTP.instances[0]
    assert smtp.host == "smtp.gmail.com"
    assert smtp.port == 587
    assert smtp.timeout == email_delivery_module._SMTP_TIMEOUT_SECONDS
    assert smtp.started_tls is True
    assert smtp.logged_in == ("reportes@meetingdoctors.com", "app-password")
    assert smtp.sent_message["To"] == "destino@cliente.com"
    assert smtp.sent_message["From"] == "reportes@meetingdoctors.com"
    assert smtp.sent_message.get_payload()[1].get_filename() == "r.csv"


def test_send_uses_ssl_not_starttls_for_port_465(monkeypatch):
    """Puerto 465 (TLS implicito, ej. relays de Gmail) necesita SMTP_SSL, no
    SMTP+starttls -- conectar en plaintext a un puerto que solo habla TLS
    desde el arranque cuelga la conexion (ver hallazgo del code review)."""
    FakeSMTP.instances = []
    monkeypatch.setattr(email_delivery_module.smtplib, "SMTP_SSL", FakeSMTP)

    secret = {**VALID_SECRET, "port": 465}
    delivery = EmailDelivery(FakeSecretManager(secret))
    result = delivery.send([], _report(), "acme", ["a@b.com"])

    assert result.status == "sent"
    smtp = FakeSMTP.instances[0]
    assert smtp.port == 465
    assert smtp.started_tls is False  # SMTP_SSL ya es TLS -- starttls() no se llama


def test_send_missing_secret_returns_failed_not_exception():
    delivery = EmailDelivery(FakeSecretManager(raise_exc=RuntimeError("secret no encontrado")))

    result = delivery.send([], _report(), "acme", ["a@b.com"])

    assert result.status == "failed"
    assert "secret no encontrado" in result.detail


def test_send_malformed_secret_missing_from_address_returns_failed_not_exception():
    """Un secret sin 'from_address' rompia la construccion del mensaje --
    ese codigo vivia afuera de cualquier try/except y tumbaba send() con un
    KeyError sin controlar (ver hallazgo del code review)."""
    incomplete_secret = {k: v for k, v in VALID_SECRET.items() if k != "from_address"}
    delivery = EmailDelivery(FakeSecretManager(incomplete_secret))

    result = delivery.send([], _report(), "acme", ["a@b.com"])

    assert result.status == "failed"
    assert "from_address" in result.detail


def test_send_smtp_connection_error_returns_failed(monkeypatch):
    class BoomSMTP:
        def __init__(self, host, port, timeout=None):
            raise ConnectionRefusedError("conexion rechazada")

    monkeypatch.setattr(email_delivery_module.smtplib, "SMTP", BoomSMTP)

    delivery = EmailDelivery(FakeSecretManager(VALID_SECRET))
    result = delivery.send([], _report(), "acme", ["a@b.com"])

    assert result.status == "failed"
    assert "conexion rechazada" in result.detail
