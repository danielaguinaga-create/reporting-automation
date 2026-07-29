from __future__ import annotations

import pandas as pd

from reporting_automation.llm.sql_generator import ChatModel

_PREVIEW_ROWS = 30

_SYSTEM_PROMPT = """Eres un asistente que responde en espanol, en 2 a 4 frases, la pregunta \
original de un usuario de negocio usando el resultado ya ejecutado de una consulta SQL. Cita \
cifras concretas del resultado cuando existan. No repitas la consulta SQL ni expliques como \
se construyo. Si el resultado no tiene filas, dilo explicitamente en vez de inventar un numero."""


def _describe_dataframe(df: pd.DataFrame) -> str:
    if df.empty:
        return "La consulta no devolvio filas."

    preview = df.head(_PREVIEW_ROWS)
    lines = [
        f"Filas totales: {len(df)}. Columnas: {', '.join(df.columns.astype(str))}.",
        "Primeras filas (CSV):",
        preview.to_csv(index=False),
    ]
    if len(df) > _PREVIEW_ROWS:
        lines.append(f"(... y {len(df) - _PREVIEW_ROWS} filas mas, no mostradas aqui)")
    return "\n".join(lines)


def summarize_answer(question: str, sql: str, df: pd.DataFrame, model: ChatModel) -> str:
    user = (
        f"Pregunta original: {question}\n\n"
        f"Consulta SQL ejecutada:\n{sql}\n\n"
        f"Resultado:\n{_describe_dataframe(df)}"
    )
    return model.generate(system=_SYSTEM_PROMPT, user=user).strip()
