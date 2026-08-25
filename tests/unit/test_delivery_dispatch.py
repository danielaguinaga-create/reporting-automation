from reporting_automation.config.models import (
    DeliveryChannel,
    OutputFormat,
    ReportConfig,
    ReportKind,
)
from reporting_automation.delivery.base import DeliveryResult
from reporting_automation.delivery.dispatch import dispatch_delivery, resolve_recipients


def _report(**overrides) -> ReportConfig:
    defaults = dict(
        id="r1",
        name="ReporteTest",
        kind=ReportKind.CUSTOM,
        client_id="acme",
        sql_file="r1.sql",
        output_formats=[OutputFormat.CSV],
        default_recipients=["default@x.com"],
    )
    defaults.update(overrides)
    return ReportConfig(**defaults)


class FakeDelivery:
    def __init__(self, channel: DeliveryChannel):
        self.channel = channel
        self.calls: list = []

    def send(self, files, report, client_id, recipients) -> DeliveryResult:
        self.calls.append((files, report.id, client_id, recipients))
        return DeliveryResult(channel=self.channel, status="sent", detail="ok")


def test_resolve_recipients_prefers_override():
    report = _report()
    assert resolve_recipients(report, ["override@x.com"]) == ["override@x.com"]


def test_resolve_recipients_falls_back_to_default_recipients():
    report = _report()
    assert resolve_recipients(report, None) == ["default@x.com"]
    assert resolve_recipients(report, []) == ["default@x.com"]


def test_dispatch_delivery_calls_each_configured_channel():
    email = FakeDelivery(DeliveryChannel.EMAIL)
    gdrive = FakeDelivery(DeliveryChannel.GDRIVE)
    report = _report(delivery_channels=[DeliveryChannel.EMAIL, DeliveryChannel.GDRIVE])

    results = dispatch_delivery(
        report, [], ["a@b.com"], "acme",
        {DeliveryChannel.EMAIL: email, DeliveryChannel.GDRIVE: gdrive},
    )

    assert [r.status for r in results] == ["sent", "sent"]
    assert email.calls == [([], "r1", "acme", ["a@b.com"])]
    assert gdrive.calls == [([], "r1", "acme", ["a@b.com"])]


def test_dispatch_delivery_unimplemented_channel_returns_failed_not_exception():
    report = _report(delivery_channels=[DeliveryChannel.FTP])

    results = dispatch_delivery(report, [], ["a@b.com"], "acme", {})

    assert len(results) == 1
    assert results[0].status == "failed"
    assert results[0].channel == DeliveryChannel.FTP


def test_dispatch_delivery_no_channels_returns_empty_list():
    report = _report(delivery_channels=[])
    assert dispatch_delivery(report, [], ["a@b.com"], "acme", {}) == []


def test_dispatch_delivery_missing_factory_for_implemented_channel_returns_failed():
    report = _report(delivery_channels=[DeliveryChannel.EMAIL])
    results = dispatch_delivery(report, [], ["a@b.com"], "acme", {})
    assert results[0].status == "failed"


class BoomDelivery:
    def send(self, files, report, client_id, recipients) -> DeliveryResult:
        raise RuntimeError("boom")


def test_dispatch_delivery_one_channel_crashing_does_not_abort_the_others():
    """Un canal que levanta una excepcion inesperada (ej. secret mal
    formado, archivo inaccesible) no debe abortar dispatch_delivery entero
    -- los canales restantes tienen que seguir intentandose (ver hallazgo
    del code review)."""
    gdrive = FakeDelivery(DeliveryChannel.GDRIVE)
    report = _report(delivery_channels=[DeliveryChannel.EMAIL, DeliveryChannel.GDRIVE])

    results = dispatch_delivery(
        report, [], ["a@b.com"], "acme",
        {DeliveryChannel.EMAIL: BoomDelivery(), DeliveryChannel.GDRIVE: gdrive},
    )

    assert [r.status for r in results] == ["failed", "sent"]
    assert results[0].channel == DeliveryChannel.EMAIL
    assert "boom" in results[0].detail
    assert gdrive.calls == [([], "r1", "acme", ["a@b.com"])]
