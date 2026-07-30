import pytest

from reporting_automation.rendering.template_registry import TemplateNotFoundError, TemplateRegistry


def test_load_discovers_html_j2_templates(tmp_path):
    (tmp_path / "branded.html.j2").write_text("<html></html>")
    (tmp_path / "otro.html.j2").write_text("<html></html>")
    (tmp_path / "ignorar.txt").write_text("no es plantilla")

    registry = TemplateRegistry()
    registry.load(tmp_path)

    assert registry.list_all() == ["branded", "otro"]


def test_get_returns_path(tmp_path):
    path = tmp_path / "branded.html.j2"
    path.write_text("<html></html>")

    registry = TemplateRegistry()
    registry.load(tmp_path)

    assert registry.get("branded") == path


def test_get_unknown_template_raises_clear_error(tmp_path):
    registry = TemplateRegistry()
    registry.load(tmp_path)

    with pytest.raises(TemplateNotFoundError, match="no_existe"):
        registry.get("no_existe")


def test_load_tolerates_missing_directory(tmp_path):
    registry = TemplateRegistry()
    registry.load(tmp_path / "no_existe_esta_carpeta")

    assert registry.list_all() == []
