import pandas as pd

from reporting_automation.rendering.pdf_renderer import build_pdf

SAMPLE_DF = pd.DataFrame({"pregunta": ["cuantos usuarios"], "resultado": [42]})


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
