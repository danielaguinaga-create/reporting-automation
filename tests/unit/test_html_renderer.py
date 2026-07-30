import pandas as pd

from reporting_automation.config.models import OutputFormat, ReportConfig, ReportKind
from reporting_automation.rendering.base import RenderContext
from reporting_automation.rendering.html_renderer import HtmlRenderer

SAMPLE_DF = pd.DataFrame({"UserToken": ["abc"], "chat_count": [3]})


def _report(**overrides) -> ReportConfig:
    defaults = dict(
        id="r1",
        name="ReporteTest",
        kind=ReportKind.CUSTOM,
        client_id="acme",
        sql_file="r1.sql",
        output_formats=[OutputFormat.HTML],
    )
    defaults.update(overrides)
    return ReportConfig(**defaults)


def test_render_without_template_uses_default(tmp_path):
    ctx = RenderContext(base_filename="out", output_dir=tmp_path)
    renderer = HtmlRenderer(templates_dir=tmp_path / "does_not_matter")

    rendered = renderer.render(SAMPLE_DF, _report(), ctx)

    assert rendered.local_path == tmp_path / "out.html"
    content = rendered.local_path.read_text(encoding="utf-8")
    assert "ReporteTest" in content
    assert "abc" in content


def test_render_with_custom_template(tmp_path):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "custom.html.j2").write_text(
        "<h1>{{ client.display_name }}</h1><span>{{ row_count }} filas</span>"
    )

    ctx = RenderContext(
        base_filename="out",
        output_dir=tmp_path,
        client_id="protec",
        client_display_name="Protec",
    )
    renderer = HtmlRenderer(templates_dir=templates_dir)

    rendered = renderer.render(SAMPLE_DF, _report(template="custom"), ctx)

    content = rendered.local_path.read_text(encoding="utf-8")
    assert "Protec" in content
    assert "1 filas" in content
