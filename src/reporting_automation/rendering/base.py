from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from reporting_automation.config.models import OutputFormat, ReportConfig

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]+')


@dataclass(frozen=True)
class RenderContext:
    base_filename: str
    output_dir: Path
    # Campos para plantillas (ver rendering/template_engine.py) -- opcionales
    # con default para no romper construcciones existentes de RenderContext.
    client_id: str = ""
    client_display_name: str | None = None
    client_branding: dict[str, str] = field(default_factory=dict)
    resolved_params: dict[str, Any] = field(default_factory=dict)
    generated_at: date = field(default_factory=date.today)


@dataclass(frozen=True)
class RenderedFile:
    format: OutputFormat
    filename: str
    local_path: Path


class Renderer(Protocol):
    def render(self, df: pd.DataFrame, report: ReportConfig, ctx: RenderContext) -> RenderedFile: ...


def safe_filename_component(text: str) -> str:
    text = _INVALID_FILENAME_CHARS.sub("_", str(text).strip())
    return re.sub(r"\s+", " ", text)


def resolve_base_filename(
    report: ReportConfig,
    params: dict[str, str],
    run_date: date | None = None,
) -> str:
    """Calcula el nombre base (sin extension) siguiendo `report.filename_pattern`.

    Si `filename_date_param` esta declarado, el year/month sale de ese
    parametro (formato `YYYY-MM-DD`, tal como lo usaba `billing_month_date`
    en el notebook). Si no, se usa la fecha de ejecucion.
    """
    if report.filename_date_param:
        raw = params.get(report.filename_date_param)
        if not raw:
            raise ValueError(
                f"filename_date_param={report.filename_date_param!r} declarado en "
                f"{report.id!r} pero no vino en params"
            )
        year, month = raw.split("-")[0], raw.split("-")[1]
    else:
        d = run_date or date.today()
        year, month = f"{d.year}", f"{d.month:02d}"

    name = report.filename_pattern.format(year=year, month=month, report_name=report.name)
    return safe_filename_component(name)
