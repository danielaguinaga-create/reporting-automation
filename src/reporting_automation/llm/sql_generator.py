from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


class ChatModel(Protocol):
    """Subconjunto minimo que este modulo necesita de un cliente de LLM.

    Permite inyectar un fake en tests sin llamar a la API de Anthropic
    (mismo patron que `QueryRunner`/`SchemaQueryRunner`).
    """

    def generate(self, *, system: str, user: str) -> str: ...


class SqlGenerationError(Exception):
    """El modelo no devolvio un bloque SQL reconocible."""


@dataclass(frozen=True)
class GeneratedSql:
    sql: str
    explanation: str


_SQL_BLOCK = re.compile(r"```sql\s*(.*?)```", re.IGNORECASE | re.DOTALL)

_SYSTEM_PROMPT_TEMPLATE = """Eres un asistente que traduce preguntas en espanol sobre una base \
de datos de BigQuery a una unica consulta SQL de solo lectura.

Proyecto: {project}
Dataset: {dataset}
Tablas disponibles (formato: tabla(columna tipo, columna tipo, ...)):
{schema}

Reglas estrictas:
1. Genera UNA sola sentencia SQL, siempre SELECT o "WITH ... SELECT". Nunca INSERT, UPDATE, \
DELETE, DROP, CREATE, ALTER ni ninguna otra sentencia de escritura o DDL.
2. Usa siempre el nombre de tabla totalmente calificado `{project}.{dataset}.<tabla>`.
3. Si la pregunta es ambigua, elige la interpretacion mas razonable para un analista de \
negocio y acompaniala de la explicacion.
4. Si la consulta no agrega datos (no usa COUNT/SUM/AVG/GROUP BY) y podria devolver muchas \
filas, agrega `LIMIT 1000`.
5. Devuelve la consulta dentro de un bloque de codigo con la etiqueta sql, exactamente asi:
```sql
SELECT ...
```
6. Despues del bloque, en un parrafo aparte y en espanol, explica brevemente que hace la \
consulta y por que elegiste esas tablas/columnas.
"""


def generate_sql(
    question: str,
    schema_text: str,
    model: ChatModel,
    project: str,
    dataset: str,
) -> GeneratedSql:
    system = _SYSTEM_PROMPT_TEMPLATE.format(project=project, dataset=dataset, schema=schema_text)
    raw = model.generate(system=system, user=f"Pregunta: {question}")

    match = _SQL_BLOCK.search(raw)
    if not match:
        raise SqlGenerationError(
            "El modelo no devolvio un bloque ```sql``` reconocible. Respuesta cruda:\n" + raw
        )

    sql = match.group(1).strip()
    explanation = (raw[: match.start()] + raw[match.end() :]).strip()
    return GeneratedSql(sql=sql, explanation=explanation or "(sin explicacion)")
