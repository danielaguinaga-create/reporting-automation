import time

from reporting_automation.llm.schema_introspection import (
    ColumnInfo,
    TableSchema,
    fetch_dataset_schema,
    format_schema_for_prompt,
    get_schema,
    load_cached_schema,
    save_schema_cache,
)


class FakeRow(dict):
    def __getitem__(self, key):
        return dict.__getitem__(self, key)


class FakeSchemaClient:
    def __init__(self, rows: list[dict]):
        self._rows = [FakeRow(r) for r in rows]
        self.queries: list[str] = []

    def query(self, sql: str):
        self.queries.append(sql)
        return self._rows


SAMPLE_ROWS = [
    {"table_name": "usuarios", "column_name": "UserToken", "data_type": "STRING"},
    {"table_name": "usuarios", "column_name": "RegisteredAt", "data_type": "TIMESTAMP"},
    {"table_name": "chats", "column_name": "ChatId", "data_type": "STRING"},
]


def test_fetch_dataset_schema_groups_columns_by_table():
    client = FakeSchemaClient(SAMPLE_ROWS)
    tables = fetch_dataset_schema(client, "proj", "ds")

    by_name = {t.table_name: t for t in tables}
    assert set(by_name) == {"usuarios", "chats"}
    assert by_name["usuarios"].columns == [
        ColumnInfo("UserToken", "STRING"),
        ColumnInfo("RegisteredAt", "TIMESTAMP"),
    ]
    assert "proj.ds" in client.queries[0]


def test_format_schema_for_prompt_lists_tables_and_columns_sorted():
    tables = [
        TableSchema("chats", [ColumnInfo("ChatId", "STRING")]),
        TableSchema("usuarios", [ColumnInfo("UserToken", "STRING")]),
    ]
    text = format_schema_for_prompt(tables)

    lines = text.splitlines()
    assert lines[0].startswith("- chats(")
    assert lines[1].startswith("- usuarios(")
    assert "UserToken STRING" in lines[1]


def test_format_schema_for_prompt_truncates_wide_tables():
    columns = [ColumnInfo(f"col_{i}", "STRING") for i in range(5)]
    tables = [TableSchema("ancha", columns)]

    text = format_schema_for_prompt(tables, max_columns_per_table=2)

    assert "col_0" in text
    assert "col_1" in text
    assert "col_4" not in text
    assert "+3 columnas mas" in text


def test_schema_cache_roundtrips(tmp_path):
    tables = [TableSchema("usuarios", [ColumnInfo("UserToken", "STRING")])]
    cache_path = tmp_path / "schema.json"

    save_schema_cache(cache_path, tables)
    loaded = load_cached_schema(cache_path, ttl_seconds=3600)

    assert loaded == tables


def test_schema_cache_expires_after_ttl(tmp_path):
    tables = [TableSchema("usuarios", [ColumnInfo("UserToken", "STRING")])]
    cache_path = tmp_path / "schema.json"
    save_schema_cache(cache_path, tables)

    time.sleep(0.05)
    assert load_cached_schema(cache_path, ttl_seconds=0.01) is None


def test_get_schema_uses_cache_without_requerying(tmp_path):
    client = FakeSchemaClient(SAMPLE_ROWS)
    cache_path = tmp_path / "schema.json"

    get_schema(client, "proj", "ds", cache_path)
    assert len(client.queries) == 1

    get_schema(client, "proj", "ds", cache_path)
    assert len(client.queries) == 1  # segunda llamada sirvio del cache


def test_get_schema_force_refresh_requeries(tmp_path):
    client = FakeSchemaClient(SAMPLE_ROWS)
    cache_path = tmp_path / "schema.json"

    get_schema(client, "proj", "ds", cache_path)
    get_schema(client, "proj", "ds", cache_path, force_refresh=True)

    assert len(client.queries) == 2
