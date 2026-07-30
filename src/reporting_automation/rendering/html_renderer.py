from __future__ import annotations

from pathlib import Path

import pandas as pd

from reporting_automation.config.models import OutputFormat, ReportConfig
from reporting_automation.rendering.base import RenderContext, RenderedFile
from reporting_automation.rendering.template_engine import (
    build_template_context,
    render_default_template,
    render_user_template,
)


class HtmlRenderer:
    """Si `report.template` esta seteado, renderiza esa plantilla de
    `config/templates/`; si no, usa la plantilla default empaquetada.

    `templates_dir` se resuelve perezosamente via `settings.templates_dir`
    en el factory (ver `rendering/factory.py`) para no acoplar este modulo
    a `config.loader`.
    """

    def __init__(self, templates_dir: str | Path = "config/templates") -> None:
        self._templates_dir = templates_dir

    def render(self, df: pd.DataFrame, report: ReportConfig, ctx: RenderContext) -> RenderedFile:
        context = build_template_context(df, report, ctx)

        if report.template:
            html = render_user_template(report.template, context, self._templates_dir)
        else:
            html = render_default_template(context)

        filename = f"{ctx.base_filename}.html"
        path = ctx.output_dir / filename
        path.write_text(html, encoding="utf-8")
        return RenderedFile(format=OutputFormat.HTML, filename=filename, local_path=path)
