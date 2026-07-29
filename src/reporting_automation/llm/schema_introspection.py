from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


class SchemaQueryRunner(Protocol):
    """Subconjunto de `bigquery.Client` que este modulo necesita.

    Permite inyectar un fake en tests sin tocar red ni credenciales (mismo
    patron que `QueryRunner` en `query/bigquery_client.py`).
    """

    def query(self, sql: str) -> Any: ...


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str


@dataclass(frozen=True)
class TableSchema:
    table_name: str
    columns: list[ColumnInfo]


_INFORMATION_SCHEMA_QUERY = """
SELECT table_name, column_name, data_type
FROM `{project}.{dataset}`.INFORMATION_SCHEMA.COLUMNS
ORDER BY table_name, ordinal_position
"""


def fetch_dataset_schema(client: SchemaQueryRunner, project: str, dataset: str) -> list[TableSchema]:
    """Lee tabla/columna/tipo del dataset via INFORMATION_SCHEMA.COLUMNS.

    Una sola query cubre todas las tablas del dataset -- mucho mas barato
    que llamar `get_table()` tabla por tabla.
    """
    sql = _INFORMATION_SCHEMA_QUERY.format(project=project, dataset=dataset)
    rows = list(client.query(sql))

    tables: dict[str, list[ColumnInfo]] = {}
    for row in rows:
        tables.setdefault(row["table_name"], []).append(
            ColumnInfo(name=row["column_name"], data_type=row["data_type"])
        )
    return [TableSchema(table_name=name, columns=cols) for name, cols in tables.items()]


def format_schema_for_prompt(tables: list[TableSchema], max_columns_per_table: int = 60) -> str:
    """Serializa el esquema a texto compacto para el prompt del LLM.

    `max_columns_per_table` evita que una tabla con cientos de columnas
    (frecuente en tablas de eventos/logs) infle el prompt sin necesidad --
    el LLM igual puede pedir el resto si la pregunta lo requiere.
    """
    lines = []
    for table in sorted(tables, key=lambda t: t.table_name):
        visible = table.columns[:max_columns_per_table]
        col_text = ", ".join(f"{c.name} {c.data_type}" for c in visible)
        omitted = len(table.columns) - len(visible)
        suffix = f", ... (+{omitted} columnas mas)" if omitted > 0 else ""
        lines.append(f"- {table.table_name}({col_text}{suffix})")
    return "\n".join(lines)


def _tables_to_json(tables: list[TableSchema]) -> dict:
    return {
        "fetched_at": time.time(),
        "tables": [
            {"table_name": t.table_name, "columns": [asdict(c) for c in t.columns]} for t in tables
        ],
    }


def _tables_from_json(payload: dict) -> list[TableSchema]:
    return [
        TableSchema(table_name=t["table_name"], columns=[ColumnInfo(**c) for c in t["columns"]])
        for t in payload["tables"]
    ]


def load_cached_schema(cache_path: Path, ttl_seconds: int) -> list[TableSchema] | None:
    if not cache_path.is_file():
        return None
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    if time.time() - payload["fetched_at"] > ttl_seconds:
        return None
    return _tables_from_json(payload)


def save_schema_cache(cache_path: Path, tables: list[TableSchema]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(_tables_to_json(tables)), encoding="utf-8")


def get_schema(
    client: SchemaQueryRunner,
    project: str,
    dataset: str,
    cache_path: Path,
    ttl_seconds: int = 24 * 3600,
    force_refresh: bool = False,
) -> list[TableSchema]:
    """Esquema del dataset, cacheado localmente (`ttl_seconds`, default 24h).

    Releer INFORMATION_SCHEMA en cada pregunta seria un costo/latencia
    innecesarios si el esquema no cambio -- `--refresh-schema` en la CLI
    fuerza `force_refresh=True` cuando alguien sabe que si cambio.
    """
    if not force_refresh:
        cached = load_cached_schema(cache_path, ttl_seconds)
        if cached is not None:
            return cached
    tables = fetch_dataset_schema(client, project, dataset)
    save_schema_cache(cache_path, tables)
    return tables
