from __future__ import annotations

import pandas as pd

from reporting_automation.config.models import OutputFormat, ReportConfig
from reporting_automation.rendering.base import RenderContext, RenderedFile


def _strip_timezones(df: pd.DataFrame) -> pd.DataFrame:
    """Excel no soporta datetimes con timezone (falla al escribir con openpyxl).

    BigQuery devuelve TIMESTAMP como datetime64 tz-aware (UTC), asi que
    cualquier reporte con una columna de fecha lo pisa si no se limpia antes.
    El notebook original hacia esto a mano solo para el reporte que lo
    necesitaba; aqui se aplica de forma generica a cualquier columna.
    """
    df = df.copy()
    for col in df.columns:
        if isinstance(df[col].dtype, pd.DatetimeTZDtype):
            df[col] = df[col].dt.tz_localize(None)
    return df


class XlsxRenderer:
    """Mismo motor que el notebook (`openpyxl`) para los reportes en Excel."""

    def render(self, df: pd.DataFrame, report: ReportConfig, ctx: RenderContext) -> RenderedFile:
        filename = f"{ctx.base_filename}.xlsx"
        path = ctx.output_dir / filename
        _strip_timezones(df).to_excel(path, index=False, engine="openpyxl")
        return RenderedFile(format=OutputFormat.XLSX, filename=filename, local_path=path)
