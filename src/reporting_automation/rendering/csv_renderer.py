from __future__ import annotations

import pandas as pd

from reporting_automation.config.models import OutputFormat, ReportConfig
from reporting_automation.rendering.base import RenderContext, RenderedFile


class CsvRenderer:
    """Preserva el `utf-8-sig` del notebook para que Excel reconozca acentos."""

    def render(self, df: pd.DataFrame, report: ReportConfig, ctx: RenderContext) -> RenderedFile:
        filename = f"{ctx.base_filename}.csv"
        path = ctx.output_dir / filename
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return RenderedFile(format=OutputFormat.CSV, filename=filename, local_path=path)
