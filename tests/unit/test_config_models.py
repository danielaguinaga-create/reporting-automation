import pytest
from pydantic import ValidationError

from reporting_automation.config.models import DeliveryChannel, OutputFormat, ReportConfig, ReportKind


def _report(**overrides) -> dict:
    defaults = dict(
        id="r1",
        name="ReporteTest",
        kind=ReportKind.CUSTOM,
        client_id="acme",
        sql_file="r1.sql",
        output_formats=[OutputFormat.CSV],
    )
    defaults.update(overrides)
    return defaults


def test_delivery_channels_rejects_duplicates():
    """dispatch_delivery() no deduplica -- un canal repetido manda el mismo
    reporte dos veces por ese canal, asi que se rechaza al cargar la config."""
    with pytest.raises(ValidationError, match="repetidos"):
        ReportConfig(**_report(delivery_channels=[DeliveryChannel.EMAIL, DeliveryChannel.EMAIL]))


def test_delivery_channels_allows_distinct_channels():
    report = ReportConfig(**_report(delivery_channels=[DeliveryChannel.EMAIL, DeliveryChannel.GDRIVE]))
    assert report.delivery_channels == [DeliveryChannel.EMAIL, DeliveryChannel.GDRIVE]


def test_delivery_channels_defaults_to_empty():
    report = ReportConfig(**_report())
    assert report.delivery_channels == []
