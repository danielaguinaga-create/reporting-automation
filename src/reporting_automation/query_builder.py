from __future__ import annotations

from dataclasses import dataclass, field

from reporting_automation.llm.schema_introspection import TableSchema

_ID_PREFIX = "id"

# Funciones de agregacion soportadas por los "campos calculados" del
# constructor visual -- mapeadas a la sintaxis SQL real que generan.
AGGREGATE_FUNCTIONS = ("COUNT", "COUNT_DISTINCT", "SUM", "AVG", "MIN", "MAX")

# Granularidades soportadas para agrupar una columna de fecha (DATE_TRUNC).
DATE_BUCKET_GRANULARITIES = ("DAY", "WEEK", "MONTH", "YEAR")

# Operadores soportados en los campos condicionales (CASE WHEN).
CASE_OPERATORS = ("=", "!=", ">", "<", ">=", "<=")

# Funciones de texto soportadas.
TEXT_FUNCTIONS = ("UPPER", "LOWER", "CONCAT")


@dataclass(frozen=True)
class JoinSpec:
    """Une `right_table` (una tabla nueva) a `left_table` (ya presente en el
    reporte) por igualdad de columnas. El orden importa: `build_sql` arma los
    JOIN en el mismo orden que esta lista, y cada uno solo puede referenciar
    tablas ya introducidas antes -- así se evita un grafo de joins invalido
    por construccion."""

    left_table: str
    left_column: str
    right_table: str
    right_column: str
    join_type: str = "INNER"


@dataclass(frozen=True)
class CalculatedField:
    """Un campo agregado, ej. COUNT(*) AS Beneficiarios o SUM(t.Monto) AS Total.
    `column` es None solo cuando `function == "COUNT"` (cuenta filas, no una
    columna puntual). `round_decimals`, si se indica, envuelve el resultado
    en ROUND(<expresion>, <decimales>)."""

    function: str
    alias: str
    column: str | None = None
    table: str | None = None
    round_decimals: int | None = None


@dataclass(frozen=True)
class DateBucketSpec:
    """Agrupa una columna de fecha por dia/semana/mes/anio, ej.
    DATE_TRUNC(t.ChatSentAtUTC, MONTH) AS Mes -- pensado para reportes
    "cantidad de X por mes". Participa en el SELECT y, si hay campos
    calculados, siempre entra al GROUP BY (es justamente para eso)."""

    table: str
    column: str
    granularity: str
    alias: str


@dataclass(frozen=True)
class CaseFieldSpec:
    """Un campo condicional simple: CASE WHEN t.col OP 'valor' THEN 'entonces'
    ELSE 'sino' END AS alias -- para clasificar filas sin escribir SQL."""

    table: str
    column: str
    operator: str
    value: str
    then_value: str
    else_value: str
    alias: str


@dataclass(frozen=True)
class TextFieldSpec:
    """Una funcion de texto: UPPER/LOWER sobre una columna, o CONCAT de
    varias. `column`/`table` se usan para UPPER/LOWER; `concat_parts` (lista
    de pares tabla/columna) se usa para CONCAT."""

    function: str
    alias: str
    table: str | None = None
    column: str | None = None
    concat_parts: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class QueryBuilderSpec:
    """Lo que junta el constructor visual antes de armar el SQL -- ningun
    campo acepta texto libre de SQL salvo los valores de comparacion de los
    campos condicionales, todo lo demas son nombres de tabla/columna reales
    del esquema, para que el resultado sea siempre sintacticamente valido."""

    tables: list[str]
    """Tablas en el reporte, en el orden en que se agregaron. La primera es
    la tabla base (el FROM); el resto se unen via `joins`."""
    columns: dict[str, list[str]] = field(default_factory=dict)
    """Columnas a incluir en el SELECT, por tabla."""
    joins: list[JoinSpec] = field(default_factory=list)
    filter_by_company: bool = False
    company_table: str | None = None
    company_column: str | None = None
    time_window_table: str | None = None
    time_window_column: str | None = None
    calculated_fields: list[CalculatedField] = field(default_factory=list)
    date_buckets: list[DateBucketSpec] = field(default_factory=list)
    case_fields: list[CaseFieldSpec] = field(default_factory=list)
    text_fields: list[TextFieldSpec] = field(default_factory=list)
    group_by: list[tuple[str, str]] | None = None
    """Control explicito de que columnas crudas (de `columns`) entran al
    GROUP BY cuando hay campos calculados. `None` (default) agrupa por todas
    -- el comportamiento automatico de siempre. Una lista (incluso vacia)
    agrupa solo por esas columnas; el resto se envuelve en ANY_VALUE(...)
    para que el SQL siga siendo valido sin forzar el agrupamiento."""


def suggest_join_columns(tables_by_name: dict[str, TableSchema], table_a: str, table_b: str) -> list[str]:
    """Columnas con el mismo nombre en ambas tablas -- candidatas a join,
    ya que este dataset no declara foreign keys (ver INFORMATION_SCHEMA.
    KEY_COLUMN_USAGE, vacio). Las que empiezan con "id" van primero: son la
    convencion de clave que ya usan todos los reportes existentes."""
    cols_a = {c.name for c in tables_by_name[table_a].columns}
    cols_b = {c.name for c in tables_by_name[table_b].columns}
    shared = cols_a & cols_b
    return sorted(shared, key=lambda name: (not name.startswith(_ID_PREFIX), name))


def _quote_table(project: str, dataset: str, table: str) -> str:
    return f"`{project}.{dataset}.{table}`"


def _escape_literal(value: str) -> str:
    """Escapa un literal de texto para BigQuery Standard SQL, que usa
    backslash (`\\'`) y no duplicado de comillas (`''`, la convencion ANSI
    de otros motores) -- duplicar comillas no cierra el literal como
    corresponde en BigQuery, asi que hay que escapar la barra invertida
    primero para no dejar un `\\` suelto justo antes de la comilla que se
    agrega despues."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def build_sql(spec: QueryBuilderSpec, project: str, dataset: str) -> str:
    """Arma el SQL de forma 100% deterministica a partir del `QueryBuilderSpec`
    -- sin IA, sin texto libre: cada pieza ya es un nombre de tabla/columna
    validado por el picker de la UI contra el esquema real de BigQuery."""
    if not spec.tables:
        raise ValueError("Elegí al menos una tabla para el reporte.")

    aliases = {table: f"t{i}" for i, table in enumerate(spec.tables)}

    raw_columns: list[tuple[str, str]] = [
        (table, col) for table in spec.tables for col in spec.columns.get(table, [])
    ]
    has_extras = bool(spec.date_buckets or spec.case_fields or spec.text_fields)
    if not raw_columns and not spec.calculated_fields and not has_extras:
        raise ValueError("Elegí al menos una columna, o agregá un campo calculado.")

    has_aggregates = bool(spec.calculated_fields)
    group_by_set = None if spec.group_by is None else set(spec.group_by)

    select_parts: list[str] = []
    group_by_refs: list[str] = []

    for table, col in raw_columns:
        qualified = f"{aliases[table]}.{col}"
        excluded = has_aggregates and group_by_set is not None and (table, col) not in group_by_set
        if excluded:
            select_parts.append(f"ANY_VALUE({qualified}) AS {col}")
        else:
            select_parts.append(qualified)
            if has_aggregates:
                group_by_refs.append(qualified)

    for bucket in spec.date_buckets:
        if bucket.granularity not in DATE_BUCKET_GRANULARITIES:
            raise ValueError(f"Granularidad de fecha no soportada: {bucket.granularity!r}")
        qualified = f"{aliases[bucket.table]}.{bucket.column}"
        select_parts.append(f"DATE_TRUNC({qualified}, {bucket.granularity}) AS {bucket.alias}")
        if has_aggregates:
            group_by_refs.append(bucket.alias)

    for case in spec.case_fields:
        select_parts.append(_render_case_field(case, aliases))
        if has_aggregates:
            group_by_refs.append(case.alias)

    for text_field in spec.text_fields:
        select_parts.append(_render_text_field(text_field, aliases))
        if has_aggregates:
            group_by_refs.append(text_field.alias)

    for calc in spec.calculated_fields:
        select_parts.append(_render_calculated_field(calc, aliases))

    select_clause = ",\n    ".join(select_parts)

    base_table = spec.tables[0]
    from_clause = f"{_quote_table(project, dataset, base_table)} AS {aliases[base_table]}"

    join_lines = []
    introduced = {base_table}
    for join in spec.joins:
        if join.left_table not in introduced:
            raise ValueError(
                f"El join hacia {join.right_table!r} referencia {join.left_table!r}, que "
                "todavia no fue agregado al reporte."
            )
        join_lines.append(
            f"{join.join_type} JOIN {_quote_table(project, dataset, join.right_table)} "
            f"AS {aliases[join.right_table]} "
            f"ON {aliases[join.left_table]}.{join.left_column} = "
            f"{aliases[join.right_table]}.{join.right_column}"
        )
        introduced.add(join.right_table)

    where_clauses = []
    if spec.filter_by_company:
        if not spec.company_table or not spec.company_column:
            raise ValueError("Elegí la columna de compañía para filtrar por id_company.")
        where_clauses.append(f"{aliases[spec.company_table]}.{spec.company_column} = @id_company")
    if spec.time_window_column:
        if not spec.time_window_table:
            raise ValueError("Elegí la tabla de la columna de fecha para la ventana de tiempo.")
        where_clauses.append(
            f"{aliases[spec.time_window_table]}.{spec.time_window_column} "
            "BETWEEN @start_date AND @end_date"
        )

    lines = ["SELECT", f"    {select_clause}", f"FROM {from_clause}"]
    lines.extend(join_lines)
    if where_clauses:
        lines.append("WHERE " + "\n  AND ".join(where_clauses))
    if group_by_refs:
        group_by_clause = ", ".join(group_by_refs)
        lines.append(f"GROUP BY {group_by_clause}")
        lines.append(f"ORDER BY {group_by_clause}")

    return "\n".join(lines) + ";"


def _render_calculated_field(calc: CalculatedField, aliases: dict[str, str]) -> str:
    if calc.function == "COUNT" and calc.column is None:
        expr = "COUNT(*)"
    else:
        if not calc.table or not calc.column:
            raise ValueError(f"El campo calculado {calc.alias!r} necesita una tabla y una columna.")
        qualified = f"{aliases[calc.table]}.{calc.column}"
        if calc.function == "COUNT_DISTINCT":
            expr = f"COUNT(DISTINCT {qualified})"
        elif calc.function in {"SUM", "AVG", "MIN", "MAX", "COUNT"}:
            expr = f"{calc.function}({qualified})"
        else:
            raise ValueError(f"Funcion de agregacion no soportada: {calc.function!r}")
    if calc.round_decimals is not None:
        expr = f"ROUND({expr}, {calc.round_decimals})"
    return f"{expr} AS {calc.alias}"


def _render_case_field(case: CaseFieldSpec, aliases: dict[str, str]) -> str:
    if case.operator not in CASE_OPERATORS:
        raise ValueError(f"Operador no soportado: {case.operator!r}")
    qualified = f"{aliases[case.table]}.{case.column}"
    value = _escape_literal(case.value)
    then_value = _escape_literal(case.then_value)
    else_value = _escape_literal(case.else_value)
    return (
        f"CASE WHEN {qualified} {case.operator} '{value}' "
        f"THEN '{then_value}' ELSE '{else_value}' END AS {case.alias}"
    )


def _render_text_field(text_field: TextFieldSpec, aliases: dict[str, str]) -> str:
    if text_field.function in {"UPPER", "LOWER"}:
        if not text_field.table or not text_field.column:
            raise ValueError(f"El campo de texto {text_field.alias!r} necesita una tabla y una columna.")
        qualified = f"{aliases[text_field.table]}.{text_field.column}"
        return f"{text_field.function}({qualified}) AS {text_field.alias}"
    if text_field.function == "CONCAT":
        if len(text_field.concat_parts) < 2:
            raise ValueError(
                f"El campo de texto {text_field.alias!r} necesita al menos dos columnas para CONCAT."
            )
        parts = ", ".join(f"{aliases[t]}.{c}" for t, c in text_field.concat_parts)
        return f"CONCAT({parts}) AS {text_field.alias}"
    raise ValueError(f"Funcion de texto no soportada: {text_field.function!r}")
