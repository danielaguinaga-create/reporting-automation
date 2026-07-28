from __future__ import annotations

import pandas as pd

from reporting_automation.config.models import OutputFormat, ReportConfig
from reporting_automation.rendering.base import RenderContext, RenderedFile


class TxtRenderer:
    """Volcado tabular simple separado por tabs.

    Ninguno de los 14 reportes migrados del notebook usa `txt` hoy; este
    formato no tiene un caso de uso real que replicar todavia, asi que la
    implementacion es deliberadamente minima. Ver README (roadmap) para
    definir el formato exacto (ancho fijo, delimitador, encabezados) cuando
    aparezca un consumidor real.
    """

    def render(self, df: pd.DataFrame, report: ReportConfig, ctx: RenderContext) -> RenderedFile:
        filename = f"{ctx.base_filename}.txt"
        path = ctx.output_dir / filename
        df.to_csv(path, index=False, sep="\t", encoding="utf-8")
        return RenderedFile(format=OutputFormat.TXT, filename=filename, local_path=path)
