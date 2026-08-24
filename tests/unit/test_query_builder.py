import pytest

from reporting_automation.llm.schema_introspection import ColumnInfo, TableSchema
from reporting_automation.query_builder import (
    CalculatedField,
    CaseFieldSpec,
    DateBucketSpec,
    JoinSpec,
    QueryBuilderSpec,
    TextFieldSpec,
    build_sql,
    suggest_join_columns,
)

_TABLES_BY_NAME = {
    "DimUsers": TableSchema(
        table_name="DimUsers",
        columns=[
            ColumnInfo(name="idUser", data_type="STRING"),
            ColumnInfo(name="idCompany", data_type="STRING"),
            ColumnInfo(name="UserType", data_type="STRING"),
            ColumnInfo(name="UserSubscribedAtUTC", data_type="TIMESTAMP"),
        ],
    ),
    "FactChatConsultations": TableSchema(
        table_name="FactChatConsultations",
        columns=[
            ColumnInfo(name="idUser", data_type="STRING"),
            ColumnInfo(name="idSpeciality", data_type="STRING"),
            ColumnInfo(name="ChatSentAtUTC", data_type="TIMESTAMP"),
        ],
    ),
    "DimSpecialities": TableSchema(
        table_name="DimSpecialities",
        columns=[
            ColumnInfo(name="idSpeciality", data_type="STRING"),
            ColumnInfo(name="SpecialityES", data_type="STRING"),
        ],
    ),
}


def test_suggest_join_columns_returns_shared_names_id_first():
    suggestions = suggest_join_columns(_TABLES_BY_NAME, "DimUsers", "FactChatConsultations")
    assert suggestions == ["idUser"]


def test_suggest_join_columns_empty_when_no_shared_columns():
    assert suggest_join_columns(_TABLES_BY_NAME, "DimUsers", "DimSpecialities") == []


def test_build_sql_single_table_detail():
    spec = QueryBuilderSpec(
        tables=["DimUsers"],
        columns={"DimUsers": ["UserType"]},
    )
    sql = build_sql(spec, "proj", "ds")

    assert sql == "SELECT\n    t0.UserType\nFROM `proj.ds.DimUsers` AS t0;"


def test_build_sql_with_company_and_time_window_filters():
    spec = QueryBuilderSpec(
        tables=["DimUsers"],
        columns={"DimUsers": ["UserType"]},
        filter_by_company=True,
        company_table="DimUsers",
        company_column="idCompany",
        time_window_table="DimUsers",
        time_window_column="UserSubscribedAtUTC",
    )
    sql = build_sql(spec, "proj", "ds")

    assert "WHERE t0.idCompany = @id_company" in sql
    assert "AND t0.UserSubscribedAtUTC BETWEEN @start_date AND @end_date" in sql


def test_build_sql_with_join():
    spec = QueryBuilderSpec(
        tables=["DimUsers", "FactChatConsultations"],
        columns={"DimUsers": ["UserType"], "FactChatConsultations": ["ChatSentAtUTC"]},
        joins=[
            JoinSpec(
                left_table="DimUsers",
                left_column="idUser",
                right_table="FactChatConsultations",
                right_column="idUser",
            )
        ],
    )
    sql = build_sql(spec, "proj", "ds")

    assert "FROM `proj.ds.DimUsers` AS t0" in sql
    assert (
        "INNER JOIN `proj.ds.FactChatConsultations` AS t1 ON t0.idUser = t1.idUser" in sql
    )
    assert "t1.ChatSentAtUTC" in sql


def test_build_sql_multi_hop_join_chain():
    spec = QueryBuilderSpec(
        tables=["DimUsers", "FactChatConsultations", "DimSpecialities"],
        columns={"DimSpecialities": ["SpecialityES"]},
        joins=[
            JoinSpec("DimUsers", "idUser", "FactChatConsultations", "idUser"),
            JoinSpec("FactChatConsultations", "idSpeciality", "DimSpecialities", "idSpeciality"),
        ],
    )
    sql = build_sql(spec, "proj", "ds")

    assert "AS t0" in sql and "AS t1" in sql and "AS t2" in sql
    assert "ON t1.idSpeciality = t2.idSpeciality" in sql


def test_build_sql_rejects_join_referencing_table_not_yet_introduced():
    spec = QueryBuilderSpec(
        tables=["DimUsers", "DimSpecialities"],
        columns={"DimUsers": ["UserType"]},
        joins=[JoinSpec("FactChatConsultations", "idUser", "DimSpecialities", "idSpeciality")],
    )
    with pytest.raises(ValueError, match="todavia no fue agregado"):
        build_sql(spec, "proj", "ds")


def test_build_sql_count_star_calculated_field_groups_by_raw_columns():
    spec = QueryBuilderSpec(
        tables=["DimUsers"],
        columns={"DimUsers": ["UserType"]},
        calculated_fields=[CalculatedField(function="COUNT", alias="Beneficiarios")],
    )
    sql = build_sql(spec, "proj", "ds")

    assert "COUNT(*) AS Beneficiarios" in sql
    assert "GROUP BY t0.UserType" in sql
    assert "ORDER BY t0.UserType" in sql


def test_build_sql_count_distinct_and_sum_calculated_fields():
    spec = QueryBuilderSpec(
        tables=["DimUsers"],
        columns={},
        calculated_fields=[
            CalculatedField(function="COUNT_DISTINCT", alias="Usuarios", table="DimUsers", column="idUser"),
        ],
    )
    sql = build_sql(spec, "proj", "ds")

    assert "COUNT(DISTINCT t0.idUser) AS Usuarios" in sql
    assert "GROUP BY" not in sql  # sin columnas crudas, no hay nada por lo que agrupar


def test_build_sql_no_tables_raises():
    with pytest.raises(ValueError, match="Elegí al menos una tabla"):
        build_sql(QueryBuilderSpec(tables=[]), "proj", "ds")


def test_build_sql_no_columns_and_no_calculated_fields_raises():
    with pytest.raises(ValueError, match="Elegí al menos una columna"):
        build_sql(QueryBuilderSpec(tables=["DimUsers"], columns={}), "proj", "ds")


def test_build_sql_filter_by_company_without_column_raises():
    spec = QueryBuilderSpec(
        tables=["DimUsers"],
        columns={"DimUsers": ["UserType"]},
        filter_by_company=True,
    )
    with pytest.raises(ValueError, match="columna de compañía"):
        build_sql(spec, "proj", "ds")


def test_build_sql_explicit_group_by_wraps_excluded_columns_in_any_value():
    spec = QueryBuilderSpec(
        tables=["DimUsers"],
        columns={"DimUsers": ["UserType", "idUser"]},
        calculated_fields=[CalculatedField(function="COUNT", alias="Total")],
        group_by=[("DimUsers", "UserType")],
    )
    sql = build_sql(spec, "proj", "ds")

    assert "t0.UserType" in sql
    assert "ANY_VALUE(t0.idUser) AS idUser" in sql
    assert "GROUP BY t0.UserType" in sql
    assert "ORDER BY t0.UserType" in sql


def test_build_sql_explicit_empty_group_by_wraps_all_raw_columns():
    spec = QueryBuilderSpec(
        tables=["DimUsers"],
        columns={"DimUsers": ["UserType"]},
        calculated_fields=[CalculatedField(function="COUNT", alias="Total")],
        group_by=[],
    )
    sql = build_sql(spec, "proj", "ds")

    assert "ANY_VALUE(t0.UserType) AS UserType" in sql
    assert "GROUP BY" not in sql


def test_build_sql_calculated_field_with_round_decimals():
    spec = QueryBuilderSpec(
        tables=["DimUsers"],
        columns={},
        calculated_fields=[
            CalculatedField(
                function="AVG", alias="PromedioX", table="DimUsers", column="idUser", round_decimals=2
            )
        ],
    )
    sql = build_sql(spec, "proj", "ds")

    assert "ROUND(AVG(t0.idUser), 2) AS PromedioX" in sql


def test_build_sql_date_bucket_groups_by_alias():
    spec = QueryBuilderSpec(
        tables=["FactChatConsultations"],
        columns={},
        date_buckets=[
            DateBucketSpec(
                table="FactChatConsultations", column="ChatSentAtUTC", granularity="MONTH", alias="Mes"
            )
        ],
        calculated_fields=[CalculatedField(function="COUNT", alias="Total")],
    )
    sql = build_sql(spec, "proj", "ds")

    assert "DATE_TRUNC(t0.ChatSentAtUTC, MONTH) AS Mes" in sql
    assert "GROUP BY Mes" in sql
    assert "ORDER BY Mes" in sql


def test_build_sql_date_bucket_rejects_invalid_granularity():
    spec = QueryBuilderSpec(
        tables=["FactChatConsultations"],
        columns={},
        date_buckets=[
            DateBucketSpec(
                table="FactChatConsultations", column="ChatSentAtUTC", granularity="HOUR", alias="Mes"
            )
        ],
    )
    with pytest.raises(ValueError, match="Granularidad"):
        build_sql(spec, "proj", "ds")


def test_build_sql_case_field_renders_case_when():
    spec = QueryBuilderSpec(
        tables=["DimUsers"],
        columns={},
        case_fields=[
            CaseFieldSpec(
                table="DimUsers",
                column="UserType",
                operator="=",
                value="Premium",
                then_value="VIP",
                else_value="Regular",
                alias="Categoria",
            )
        ],
    )
    sql = build_sql(spec, "proj", "ds")

    assert (
        "CASE WHEN t0.UserType = 'Premium' THEN 'VIP' ELSE 'Regular' END AS Categoria" in sql
    )


def test_build_sql_case_field_escapes_single_quotes():
    spec = QueryBuilderSpec(
        tables=["DimUsers"],
        columns={},
        case_fields=[
            CaseFieldSpec(
                table="DimUsers",
                column="UserType",
                operator="=",
                value="O'Brien",
                then_value="si",
                else_value="no",
                alias="Categoria",
            )
        ],
    )
    sql = build_sql(spec, "proj", "ds")

    assert "t0.UserType = 'O\\'Brien'" in sql


def test_build_sql_case_field_escapes_backslash_before_quote():
    """BigQuery escapa comillas con backslash, no duplicandolas -- si no se
    escapa primero la barra invertida de un valor como `a\\`, el `\\'` que
    resulta de agregarle la comilla de cierre se lee como una comilla
    literal escapada en vez de cerrar el string, y el literal se queda
    abierto (ver hallazgo del code review)."""
    spec = QueryBuilderSpec(
        tables=["DimUsers"],
        columns={},
        case_fields=[
            CaseFieldSpec(
                table="DimUsers",
                column="UserType",
                operator="=",
                value="a\\",
                then_value="si",
                else_value="no",
                alias="Categoria",
            )
        ],
    )
    sql = build_sql(spec, "proj", "ds")

    assert "t0.UserType = 'a\\\\'" in sql


def test_build_sql_case_field_rejects_unsupported_operator():
    spec = QueryBuilderSpec(
        tables=["DimUsers"],
        columns={},
        case_fields=[
            CaseFieldSpec(
                table="DimUsers",
                column="UserType",
                operator="LIKE",
                value="x",
                then_value="y",
                else_value="z",
                alias="Categoria",
            )
        ],
    )
    with pytest.raises(ValueError, match="Operador no soportado"):
        build_sql(spec, "proj", "ds")


def test_build_sql_text_field_upper():
    spec = QueryBuilderSpec(
        tables=["DimUsers"],
        columns={},
        text_fields=[TextFieldSpec(function="UPPER", alias="TipoMayus", table="DimUsers", column="UserType")],
    )
    sql = build_sql(spec, "proj", "ds")

    assert "UPPER(t0.UserType) AS TipoMayus" in sql


def test_build_sql_text_field_concat():
    spec = QueryBuilderSpec(
        tables=["DimUsers", "FactChatConsultations"],
        columns={},
        joins=[JoinSpec("DimUsers", "idUser", "FactChatConsultations", "idUser")],
        text_fields=[
            TextFieldSpec(
                function="CONCAT",
                alias="Combinado",
                concat_parts=(("DimUsers", "idUser"), ("FactChatConsultations", "idSpeciality")),
            )
        ],
    )
    sql = build_sql(spec, "proj", "ds")

    assert "CONCAT(t0.idUser, t1.idSpeciality) AS Combinado" in sql


def test_build_sql_text_field_concat_needs_at_least_two_columns():
    spec = QueryBuilderSpec(
        tables=["DimUsers"],
        columns={},
        text_fields=[
            TextFieldSpec(function="CONCAT", alias="Combinado", concat_parts=(("DimUsers", "idUser"),))
        ],
    )
    with pytest.raises(ValueError, match="al menos dos columnas"):
        build_sql(spec, "proj", "ds")
