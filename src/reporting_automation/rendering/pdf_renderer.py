from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from reporting_automation.config.models import OutputFormat, ReportConfig
from reporting_automation.rendering.base import RenderContext, RenderedFile

_MAX_ROWS = 500  # PDF no es para volumen -- para el detalle completo, usar csv/xlsx.


def build_pdf(path: Path, title: str, df: pd.DataFrame, extra_paragraphs: list[str] | None = None) -> None:
    """Arma un PDF con titulo + parrafos opcionales + tabla (paginado en landscape).

    `title` siempre se escapa aca (viene de datos, no de markup de confianza).
    `extra_paragraphs` se inserta tal cual como XML minimo de reportlab
    (`<b>`, etc.) -- el llamador es responsable de escapar cualquier texto
    dinamico que interpole ahi (ver `ask.py`).
    """
    styles = getSampleStyleSheet()
    story = [Paragraph(_xml_escape(title), styles["Title"]), Spacer(1, 0.4 * cm)]

    for para in extra_paragraphs or []:
        story.append(Paragraph(para, styles["BodyText"]))
        story.append(Spacer(1, 0.3 * cm))

    if len(df) > _MAX_ROWS:
        story.append(
            Paragraph(
                f"Mostrando las primeras {_MAX_ROWS} de {len(df)} filas -- "
                "usa CSV o XLSX para el detalle completo.",
                styles["Italic"],
            )
        )
        story.append(Spacer(1, 0.3 * cm))
        df = df.head(_MAX_ROWS)

    data = [list(df.columns.astype(str))] + df.astype(str).values.tolist()
    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
            ]
        )
    )
    story.append(table)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=landscape(letter))
    doc.build(story)


class PdfRenderer:
    """Volcado tabular simple a PDF -- mismo rol que CsvRenderer/XlsxRenderer."""

    def render(self, df: pd.DataFrame, report: ReportConfig, ctx: RenderContext) -> RenderedFile:
        filename = f"{ctx.base_filename}.pdf"
        path = ctx.output_dir / filename
        build_pdf(path, title=report.name, df=df)
        return RenderedFile(format=OutputFormat.PDF, filename=filename, local_path=path)
