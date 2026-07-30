from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from weasyprint import HTML

from reporting_automation.config.models import OutputFormat, ReportConfig
from reporting_automation.rendering.base import RenderContext, RenderedFile
from reporting_automation.rendering.template_engine import (
    build_template_context,
    render_default_template,
    render_user_template,
)

_MAX_ROWS = 500  # PDF no es para volumen -- para el detalle completo, usar csv/xlsx.


def _html_to_pdf(html: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html).write_pdf(str(path))


def _truncate_rows(context: dict) -> dict:
    """Aplica el limite de `_MAX_ROWS` a un context ya armado (mismo
    comportamiento que el PDF de ReportLab original: recorta y deja nota)."""
    if context["row_count"] > _MAX_ROWS:
        context = {**context, "rows": context["rows"][:_MAX_ROWS], "truncated": True}
    return context


def build_pdf(
    path: Path, title: str, df: pd.DataFrame, extra_paragraphs: list[str] | None = None
) -> None:
    """Arma un PDF ad-hoc (titulo + parrafos opcionales + tabla), via la
    plantilla default empaquetada + WeasyPrint.

    Firma identica a la version anterior (ReportLab) -- `ask.py` la llama
    tal cual, sin cambios. `extra_paragraphs` se inserta como HTML de
    confianza (el llamador es responsable de escapar texto dinamico, ver
    `ask.py`).
    """
    context = {
        "title": title,
        "report": None,
        "client": None,
        "params": {},
        "generated_at": date.today(),
        "extra_paragraphs": extra_paragraphs or [],
        "rows": df.astype(object).where(pd.notnull(df), None).to_dict("records"),
        "columns": list(df.columns.astype(str)),
        "row_count": len(df),
        "truncated": False,
    }
    context = _truncate_rows(context)
    html = render_default_template(context)
    _html_to_pdf(html, path)


class PdfRenderer:
    """Mismo rol que CsvRenderer/XlsxRenderer/HtmlRenderer: si
    `report.template` esta seteado, lo usa (contexto completo: report,
    cliente, params); si no, delega a `build_pdf` (plantilla default,
    mismo look generico de siempre)."""

    def __init__(self, templates_dir: str | Path = "config/templates") -> None:
        self._templates_dir = templates_dir

    def render(self, df: pd.DataFrame, report: ReportConfig, ctx: RenderContext) -> RenderedFile:
        filename = f"{ctx.base_filename}.pdf"
        path = ctx.output_dir / filename

        if report.template:
            context = _truncate_rows(build_template_context(df, report, ctx))
            html = render_user_template(report.template, context, self._templates_dir)
            _html_to_pdf(html, path)
        else:
            build_pdf(path, title=report.name, df=df)

        return RenderedFile(format=OutputFormat.PDF, filename=filename, local_path=path)
