from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from reporting_automation.config.models import ReportConfig
from reporting_automation.exceptions import ReportConfigError, ReportNotFoundError


class ReportRegistry:
    """Descubre y valida reportes definidos como pares <id>.yaml + <sql_file>.

    Reemplaza el diccionario Python hardcodeado del notebook: cada reporte
    vive en su propio YAML (metadata) + .sql (query), bajo `reports_dir`
    (recursivo, para soportar `shared/` y `custom/`).
    """

    def __init__(self) -> None:
        self._reports: dict[str, ReportConfig] = {}
        self._sql_dirs: dict[str, Path] = {}

    def load(self, reports_dir: str | Path) -> None:
        reports_dir = Path(reports_dir)
        for yaml_path in sorted(reports_dir.rglob("*.yaml")):
            with yaml_path.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)

            try:
                report = ReportConfig.model_validate(raw)
            except ValidationError as exc:
                raise ReportConfigError(f"Config invalida en {yaml_path}: {exc}") from exc

            sql_path = yaml_path.parent / report.sql_file
            if not sql_path.is_file():
                raise ReportConfigError(
                    f"{yaml_path} referencia sql_file={report.sql_file!r}, "
                    f"pero no existe {sql_path}"
                )

            if report.id in self._reports:
                raise ReportConfigError(f"Report id duplicado: {report.id!r} ({yaml_path})")

            self._reports[report.id] = report
            self._sql_dirs[report.id] = yaml_path.parent

    def get(self, report_id: str) -> ReportConfig:
        try:
            return self._reports[report_id]
        except KeyError:
            raise ReportNotFoundError(f"No existe un reporte con id={report_id!r}") from None

    def sql_dir_for(self, report_id: str) -> Path:
        self.get(report_id)  # valida existencia con el mismo mensaje de error
        return self._sql_dirs[report_id]

    def list_all(self) -> list[ReportConfig]:
        return list(self._reports.values())

    def list_by_client(self, client_id: str) -> list[ReportConfig]:
        return [r for r in self._reports.values() if r.client_id == client_id]
