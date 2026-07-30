import pandas as pd

from reporting_automation.config.models import OutputFormat, ReportConfig, ReportKind
from reporting_automation.rendering.base import RenderContext
from reporting_automation.rendering.pdf_renderer import PdfRenderer, build_pdf

SAMPLE_DF = pd.DataFrame({"pregunta": ["cuantos usuarios"], "resultado": [42]})


def _report(**overrides) -> ReportConfig:
    defaults = dict(
        id="r1",
        name="ReporteTest",
        kind=ReportKind.CUSTOM,
        client_id="acme",
        sql_file="r1.sql",
        output_formats=[OutputFormat.PDF],
    )
    defaults.update(overrides)
    return ReportConfig(**defaults)


def test_build_pdf_with_extra_paragraphs_writes_valid_pdf(tmp_path):
    path = tmp_path / "respuesta.pdf"
    build_pdf(
        path,
        title="¿Cuántos usuarios activos hay?",
        df=SAMPLE_DF,
        extra_paragraphs=["<b>SQL generado:</b> SELECT 1", "<b>Respuesta:</b> Hay 42 usuarios."],
    )

    assert path.is_file()
    assert path.read_bytes().startswith(b"%PDF-")


def test_build_pdf_truncates_beyond_max_rows(tmp_path):
    big_df = pd.DataFrame({"n": range(600)})
    path = tmp_path / "grande.pdf"

    build_pdf(path, title="Muchas filas", df=big_df)

    assert path.is_file()
    assert path.read_bytes().startswith(b"%PDF-")


def test_build_pdf_escapes_special_characters_in_title(tmp_path):
    path = tmp_path / "escape.pdf"
    build_pdf(path, title="<script>alert(1)</script> & cosas raras", df=SAMPLE_DF)

    assert path.is_file()
    assert path.read_bytes().startswith(b"%PDF-")


def test_pdf_renderer_without_template_uses_default(tmp_path):
    ctx = RenderContext(base_filename="out", output_dir=tmp_path)
    rendered = PdfRenderer().render(SAMPLE_DF, _report(), ctx)

    assert rendered.local_path == tmp_path / "out.pdf"
    assert rendered.local_path.read_bytes().startswith(b"%PDF-")


def test_pdf_renderer_with_custom_template(tmp_path):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "custom.html.j2").write_text(
        "<html><body><h1>{{ client.display_name }}</h1>"
        "<table>{% for row in rows %}<tr><td>{{ row.pregunta }}</td></tr>{% endfor %}</table>"
        "</body></html>"
    )

    ctx = RenderContext(
        base_filename="out",
        output_dir=tmp_path,
        client_id="protec",
        client_display_name="Protec",
    )
    renderer = PdfRenderer(templates_dir=templates_dir)

    rendered = renderer.render(SAMPLE_DF, _report(template="custom"), ctx)

    assert rendered.local_path.read_bytes().startswith(b"%PDF-")


def test_pdf_renderer_truncates_rows_with_custom_template(tmp_path):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "custom.html.j2").write_text(
        "<html><body>{% if truncated %}<p>truncado</p>{% endif %}"
        "<table>{% for row in rows %}<tr><td>{{ row.n }}</td></tr>{% endfor %}</table></body></html>"
    )
    big_df = pd.DataFrame({"n": range(600)})
    ctx = RenderContext(base_filename="grande", output_dir=tmp_path)
    renderer = PdfRenderer(templates_dir=templates_dir)

    rendered = renderer.render(big_df, _report(template="custom"), ctx)

    assert rendered.local_path.read_bytes().startswith(b"%PDF-")
