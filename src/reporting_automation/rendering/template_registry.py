from __future__ import annotations

from pathlib import Path

from reporting_automation.exceptions import ReportingAutomationError


class TemplateNotFoundError(ReportingAutomationError):
    pass


class TemplateRegistry:
    """Descubre plantillas `<nombre>.html.j2` bajo `templates_dir`.

    A diferencia de reportes/clientes, una plantilla no necesita metadata
    en YAML aparte -- es solo el archivo. `ReportConfig.template` referencia
    el nombre (sin extension).
    """

    def __init__(self) -> None:
        self._templates: dict[str, Path] = {}

    def load(self, templates_dir: str | Path) -> None:
        templates_dir = Path(templates_dir)
        if not templates_dir.is_dir():
            return
        for path in sorted(templates_dir.glob("*.html.j2")):
            name = path.name.removesuffix(".html.j2")
            self._templates[name] = path

    def get(self, name: str) -> Path:
        try:
            return self._templates[name]
        except KeyError:
            raise TemplateNotFoundError(
                f"No existe una plantilla con nombre {name!r} en config/templates/"
            ) from None

    def list_all(self) -> list[str]:
        return sorted(self._templates)
