from datetime import date

import pandas as pd

from reporting_automation.config.models import OutputFormat, ReportConfig, ReportKind
from reporting_automation.rendering.base import RenderContext
from reporting_automation.rendering.template_engine import (
    build_template_context,
    render_default_template,
    render_user_template,
)

SAMPLE_DF = pd.DataFrame({"UserToken": ["abc"], "chat_count": [3]})


def _report(**overrides) -> ReportConfig:
    defaults = dict(
        id="r1",
        name="ReporteTest",
        kind=ReportKind.CUSTOM,
        client_id="acme",
        sql_file="r1.sql",
        output_formats=[OutputFormat.HTML],
        description="Una descripcion.",
    )
    defaults.update(overrides)
    return ReportConfig(**defaults)


def test_build_template_context_shape():
    ctx = RenderContext(
        base_filename="out",
        output_dir="/tmp",
        client_id="protec",
        client_display_name="Protec",
        client_branding={"logo_url": "https://x/logo.png", "primary_color": "#000"},
        resolved_params={"billing_month_date": "2026-06-01"},
        generated_at=date(2026, 7, 1),
    )

    context = build_template_context(SAMPLE_DF, _report(), ctx)

    assert context["title"] == "ReporteTest"
    assert context["report"]["id"] == "r1"
    assert context["client"]["display_name"] == "Protec"
    assert context["client"]["branding"]["logo_url"] == "https://x/logo.png"
    assert context["params"] == {"billing_month_date": "2026-06-01"}
    assert context["generated_at"] == date(2026, 7, 1)
    assert context["rows"] == [{"UserToken": "abc", "chat_count": 3}]
    assert context["columns"] == ["UserToken", "chat_count"]
    assert context["row_count"] == 1
    assert context["truncated"] is False


def test_build_template_context_without_client_id_has_no_client():
    ctx = RenderContext(base_filename="out", output_dir="/tmp")
    context = build_template_context(SAMPLE_DF, _report(), ctx)
    assert context["client"] is None


def test_render_user_template_renders_variables(tmp_path):
    (tmp_path / "simple.html.j2").write_text("<h1>{{ title }}</h1><p>{{ client.display_name }}</p>")

    html = render_user_template(
        "simple", {"title": "Hola", "client": {"display_name": "Protec"}}, tmp_path
    )

    assert "<h1>Hola</h1>" in html
    assert "Protec" in html


def test_render_user_template_autoescapes_html():
    context = {"title": "<script>alert(1)</script>", "client": None}
    # usa una plantilla temporal minima
    import pathlib
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        pathlib.Path(tmp, "x.html.j2").write_text("<h1>{{ title }}</h1>")
        html = render_user_template("x", context, tmp)

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_default_template_includes_rows_and_title():
    context = {
        "title": "Reporte X",
        "report": None,
        "client": None,
        "params": {},
        "generated_at": date(2026, 7, 1),
        "extra_paragraphs": [],
        "rows": [{"a": 1}],
        "columns": ["a"],
        "row_count": 1,
        "truncated": False,
    }

    html = render_default_template(context)

    assert "Reporte X" in html
    assert "<table" in html
