from __future__ import annotations

from reporting_automation.config.models import OutputFormat
from reporting_automation.rendering.base import Renderer
from reporting_automation.rendering.csv_renderer import CsvRenderer
from reporting_automation.rendering.html_renderer import HtmlRenderer
from reporting_automation.rendering.pdf_renderer import PdfRenderer
from reporting_automation.rendering.txt_renderer import TxtRenderer
from reporting_automation.rendering.xlsx_renderer import XlsxRenderer

_RENDERERS: dict[OutputFormat, Renderer] = {
    OutputFormat.CSV: CsvRenderer(),
    OutputFormat.XLSX: XlsxRenderer(),
    OutputFormat.TXT: TxtRenderer(),
    OutputFormat.PDF: PdfRenderer(),
    OutputFormat.HTML: HtmlRenderer(),
}

_NOT_YET_IMPLEMENTED = {OutputFormat.GSHEETS}


def get_renderer(fmt: OutputFormat) -> Renderer:
    if fmt in _NOT_YET_IMPLEMENTED:
        raise NotImplementedError(
            f"Renderer para {fmt.value!r} no esta implementado en Fase 1 "
            "(requiere decision de libreria/API pendiente, ver README)."
        )
    try:
        return _RENDERERS[fmt]
    except KeyError:
        raise ValueError(f"Formato de salida desconocido: {fmt!r}") from None
