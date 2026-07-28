from __future__ import annotations

from pathlib import Path

import yaml

from reporting_automation.config.models import ClientConfig, ReportConfig
from reporting_automation.exceptions import ClientConfigError, ReportConfigError


def scaffold_report(reports_dir: Path, report: ReportConfig, sql_text: str) -> tuple[Path, Path]:
    """Crea el par <id>.yaml + <sql_file> de un reporte ad-hoc nuevo.

    No requiere tocar codigo Python: `ReportRegistry.load()` descubre el par
    automaticamente la proxima vez que se ejecute la CLI. `report.kind`
    decide la subcarpeta (`shared/` o `custom/`) dentro de `reports_dir`.
    """
    kind_dir = Path(reports_dir) / report.kind.value
    kind_dir.mkdir(parents=True, exist_ok=True)

    yaml_path = kind_dir / f"{report.id}.yaml"
    sql_path = kind_dir / report.sql_file

    if yaml_path.exists() or sql_path.exists():
        raise ReportConfigError(
            f"Ya existe un reporte en {kind_dir} con id/sql_file={report.id!r}; "
            "elige otro --id o borra los archivos existentes primero."
        )

    sql_path.write_text(sql_text, encoding="utf-8")

    payload = report.model_dump(mode="json", exclude_none=True)
    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)

    return yaml_path, sql_path


def scaffold_client(clients_dir: Path, client: ClientConfig, overwrite: bool = False) -> Path:
    """Crea (o sobreescribe, si `overwrite=True`) `<id>.yaml` para un cliente.

    `client.bq_params` son los valores que `orchestrator.resolve_params`
    inyecta automaticamente cuando se corre `run --client <id>` para un
    reporte `shared` que declare esos mismos nombres en `params_schema`
    (ej. `id_company`).
    """
    clients_dir = Path(clients_dir)
    clients_dir.mkdir(parents=True, exist_ok=True)

    yaml_path = clients_dir / f"{client.id}.yaml"
    if yaml_path.exists() and not overwrite:
        raise ClientConfigError(
            f"Ya existe un cliente registrado en {yaml_path}; "
            "elige otro --id o borra el archivo existente primero."
        )

    payload = client.model_dump(mode="json", exclude_none=True)
    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)

    return yaml_path
