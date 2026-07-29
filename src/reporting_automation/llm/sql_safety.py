from __future__ import annotations

import re


class UnsafeSqlError(Exception):
    """El SQL generado por el LLM no paso la validacion de solo-lectura."""


_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|MERGE|TRUNCATE|GRANT|REVOKE|CALL|EXECUTE|"
    r"BEGIN|DECLARE)\b",
    re.IGNORECASE,
)

_ALLOWED_START = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def validate_readonly_sql(sql: str) -> None:
    """Primera barrera (regex, barata) contra SQL de escritura/DDL o multi-sentencia.

    Deliberadamente redundante con `confirm_statement_type_is_select` (que usa
    el `statement_type` que BigQuery devuelve en un dry run): esta funcion
    corre ANTES de gastar ninguna llamada a BigQuery, la otra es la barrera
    autoritativa despues. Ninguna reemplaza a la otra.
    """
    stripped = sql.strip()
    body = stripped.rstrip(";").rstrip()

    if ";" in body:
        raise UnsafeSqlError(
            "El SQL generado tiene mas de una sentencia -- rechazado por seguridad."
        )
    if not _ALLOWED_START.match(stripped):
        raise UnsafeSqlError(
            "El SQL generado debe empezar con SELECT o WITH -- rechazado por seguridad."
        )
    match = _FORBIDDEN_KEYWORDS.search(body)
    if match:
        raise UnsafeSqlError(
            f"El SQL generado contiene la palabra clave {match.group(0)!r} "
            "(escritura/DDL), no permitida -- rechazado por seguridad."
        )


def confirm_statement_type_is_select(statement_type: str | None) -> None:
    """Barrera autoritativa: BigQuery clasifica la query en el dry run mismo,
    sin depender de que el texto "parezca" un SELECT."""
    if statement_type is not None and statement_type != "SELECT":
        raise UnsafeSqlError(
            f"BigQuery clasifico la query generada como {statement_type!r}, no SELECT "
            "-- rechazada por seguridad."
        )
