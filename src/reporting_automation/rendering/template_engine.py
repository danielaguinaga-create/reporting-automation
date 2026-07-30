from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from reporting_automation.config.models import ReportConfig
from reporting_automation.rendering.base import RenderContext

_PACKAGE_TEMPLATES_DIR = Path(__file__).parent / "templates"


def build_template_context(df: pd.DataFrame, report: ReportConfig, ctx: RenderContext) -> dict:
    """Arma el dict que ven las plantillas Jinja2 -- nunca expone objetos
    internos de Python, solo datos planos (dicts/listas/strings).

    Incluye `title`/`extra_paragraphs`/`truncated` ademas de `report`/
    `client`/`params`, para que la plantilla default (`_default.html.j2`)
    sirva tanto a reportes registrados como al uso ad-hoc de
    `pdf_renderer.build_pdf` (ver ese modulo) con un solo set de campos.
    """
    return {
        "title": report.name,
        "report": {
            "id": report.id,
            "name": report.name,
            "description": report.description,
        },
        "client": {
            "id": ctx.client_id,
            "display_name": ctx.client_display_name or ctx.client_id,
            "branding": dict(ctx.client_branding),
        }
        if ctx.client_id
        else None,
        "params": dict(ctx.resolved_params),
        "generated_at": ctx.generated_at,
        "extra_paragraphs": [],
        "rows": df.astype(object).where(pd.notnull(df), None).to_dict("records"),
        "columns": list(df.columns.astype(str)),
        "row_count": len(df),
        "truncated": False,
    }


def _env_for(templates_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "j2"]),
    )


def render_user_template(name: str, context: dict[str, Any], templates_dir: str | Path) -> str:
    """Renderiza `<templates_dir>/<name>.html.j2` con `context`."""
    env = _env_for(Path(templates_dir))
    template = env.get_template(f"{name}.html.j2")
    return template.render(**context)


def render_default_template(context: dict[str, Any]) -> str:
    """Renderiza la plantilla default empaquetada (usada cuando
    `ReportConfig.template` es None)."""
    env = _env_for(_PACKAGE_TEMPLATES_DIR)
    template = env.get_template("_default.html.j2")
    return template.render(**context)
