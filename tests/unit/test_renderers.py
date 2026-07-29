from datetime import date

import pandas as pd
import pytest

from reporting_automation.config.models import OutputFormat, ReportConfig, ReportKind
from reporting_automation.rendering.base import RenderContext, resolve_base_filename
from reporting_automation.rendering.csv_renderer import CsvRenderer
from reporting_automation.rendering.factory import get_renderer
from reporting_automation.rendering.pdf_renderer import PdfRenderer
from reporting_automation.rendering.txt_renderer import TxtRenderer
from reporting_automation.rendering.xlsx_renderer import XlsxRenderer

SAMPLE_DF = pd.DataFrame({"UserToken": ["abc", "ñoño"], "chat_count": [3, 5]})


def _report(**overrides) -> ReportConfig:
    defaults = dict(
        id="r1",
        name="Reporte Test",
        kind=ReportKind.CUSTOM,
        client_id="acme",
        sql_file="r1.sql",
        output_formats=[OutputFormat.CSV],
    )
    defaults.update(overrides)
    return ReportConfig(**defaults)


def test_csv_renderer_uses_utf8_sig(tmp_path):
    ctx = RenderContext(base_filename="out", output_dir=tmp_path)
    rendered = CsvRenderer().render(SAMPLE_DF, _report(), ctx)

    assert rendered.local_path == tmp_path / "out.csv"
    raw = rendered.local_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM de utf-8-sig

    read_back = pd.read_csv(rendered.local_path, encoding="utf-8-sig")
    assert read_back["UserToken"].tolist() == ["abc", "ñoño"]


def test_xlsx_renderer_roundtrips(tmp_path):
    ctx = RenderContext(base_filename="out", output_dir=tmp_path)
    rendered = XlsxRenderer().render(SAMPLE_DF, _report(), ctx)

    assert rendered.local_path == tmp_path / "out.xlsx"
    read_back = pd.read_excel(rendered.local_path, engine="openpyxl")
    assert read_back["chat_count"].tolist() == [3, 5]


def test_xlsx_renderer_strips_timezone_from_datetime_columns(tmp_path):
    df = pd.DataFrame(
        {
            "UserToken": ["abc"],
            "RegisteredAtUTC": pd.to_datetime(["2026-06-01 12:00:00"]).tz_localize("UTC"),
        }
    )
    ctx = RenderContext(base_filename="out", output_dir=tmp_path)

    rendered = XlsxRenderer().render(df, _report(), ctx)

    read_back = pd.read_excel(rendered.local_path, engine="openpyxl")
    assert read_back["RegisteredAtUTC"][0] == pd.Timestamp("2026-06-01 12:00:00")
    assert df["RegisteredAtUTC"].dt.tz is not None  # el df original no se muta


def test_txt_renderer_is_tab_separated(tmp_path):
    ctx = RenderContext(base_filename="out", output_dir=tmp_path)
    rendered = TxtRenderer().render(SAMPLE_DF, _report(), ctx)

    content = rendered.local_path.read_text(encoding="utf-8")
    assert "\t" in content.splitlines()[0]


def test_factory_returns_implemented_renderers():
    assert isinstance(get_renderer(OutputFormat.CSV), CsvRenderer)
    assert isinstance(get_renderer(OutputFormat.XLSX), XlsxRenderer)
    assert isinstance(get_renderer(OutputFormat.TXT), TxtRenderer)
    assert isinstance(get_renderer(OutputFormat.PDF), PdfRenderer)


@pytest.mark.parametrize("fmt", [OutputFormat.GSHEETS])
def test_factory_raises_not_implemented_for_pending_formats(fmt):
    with pytest.raises(NotImplementedError):
        get_renderer(fmt)


def test_pdf_renderer_writes_a_valid_pdf(tmp_path):
    ctx = RenderContext(base_filename="out", output_dir=tmp_path)
    rendered = PdfRenderer().render(SAMPLE_DF, _report(), ctx)

    assert rendered.local_path == tmp_path / "out.pdf"
    raw = rendered.local_path.read_bytes()
    assert raw.startswith(b"%PDF-")
    assert rendered.local_path.stat().st_size > 0


def test_resolve_base_filename_uses_run_date_by_default():
    report = _report(name="UsuariosActivos", filename_pattern="{year}{month}_MD_{report_name}")
    name = resolve_base_filename(report, params={}, run_date=date(2026, 6, 15))
    assert name == "202606_MD_UsuariosActivos"


def test_resolve_base_filename_uses_date_param_when_declared():
    report = _report(
        name="ChatsByUser",
        filename_pattern="{year}{month}_MD_{report_name}",
        filename_date_param="billing_month_date",
        params_schema={"billing_month_date": "DATE"},
    )
    name = resolve_base_filename(
        report, params={"billing_month_date": "2026-01-01"}, run_date=date(2026, 6, 15)
    )
    assert name == "202601_MD_ChatsByUser"


def test_resolve_base_filename_missing_date_param_raises():
    report = _report(filename_date_param="billing_month_date")
    with pytest.raises(ValueError, match="filename_date_param"):
        resolve_base_filename(report, params={})
