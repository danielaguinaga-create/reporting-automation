import pytest

from reporting_automation.llm.sql_safety import (
    UnsafeSqlError,
    confirm_statement_type_is_select,
    validate_readonly_sql,
)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM `proj.ds.tabla` LIMIT 10",
        "  select id from proj.ds.tabla",
        "WITH t AS (SELECT 1 AS x) SELECT * FROM t",
        "SELECT * FROM proj.ds.tabla;",  # un ; final (terminador) se tolera
    ],
)
def test_validate_readonly_sql_accepts_select_and_with(sql):
    validate_readonly_sql(sql)  # no debe levantar


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE proj.ds.tabla",
        "DELETE FROM proj.ds.tabla WHERE 1=1",
        "INSERT INTO proj.ds.tabla VALUES (1)",
        "UPDATE proj.ds.tabla SET x = 1",
        "CREATE TABLE proj.ds.tabla2 AS SELECT * FROM proj.ds.tabla",
        "ALTER TABLE proj.ds.tabla ADD COLUMN x INT64",
        "MERGE proj.ds.tabla USING proj.ds.tabla2 ON true WHEN MATCHED THEN DELETE",
        "CALL proj.ds.some_procedure()",
    ],
)
def test_validate_readonly_sql_rejects_write_and_ddl(sql):
    with pytest.raises(UnsafeSqlError):
        validate_readonly_sql(sql)


def test_validate_readonly_sql_rejects_non_select_start():
    with pytest.raises(UnsafeSqlError):
        validate_readonly_sql("EXPLAIN SELECT * FROM proj.ds.tabla")


def test_validate_readonly_sql_rejects_multiple_statements():
    with pytest.raises(UnsafeSqlError):
        validate_readonly_sql("SELECT 1; DROP TABLE proj.ds.tabla")


def test_validate_readonly_sql_does_not_false_positive_on_column_named_like_keyword():
    # "settings" y "createdAt" no deben disparar el filtro de "SET"/"CREATE".
    validate_readonly_sql("SELECT settings, createdAt FROM proj.ds.tabla")


def test_confirm_statement_type_accepts_select():
    confirm_statement_type_is_select("SELECT")  # no debe levantar
    confirm_statement_type_is_select(None)  # no debe levantar


def test_confirm_statement_type_rejects_non_select():
    with pytest.raises(UnsafeSqlError):
        confirm_statement_type_is_select("DELETE")
