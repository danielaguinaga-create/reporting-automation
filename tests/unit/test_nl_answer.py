import pandas as pd

from reporting_automation.llm.nl_answer import summarize_answer


class FakeChatModel:
    def __init__(self, response: str):
        self._response = response
        self.last_call: dict | None = None

    def generate(self, *, system: str, user: str) -> str:
        self.last_call = {"system": system, "user": user}
        return self._response


def test_summarize_answer_includes_question_sql_and_row_count_in_prompt():
    df = pd.DataFrame({"total": [42]})
    model = FakeChatModel("Hay 42 usuarios activos.")

    answer = summarize_answer("cuantos usuarios activos hay", "SELECT COUNT(*) AS total FROM t", df, model)

    assert answer == "Hay 42 usuarios activos."
    assert "cuantos usuarios activos hay" in model.last_call["user"]
    assert "SELECT COUNT(*) AS total FROM t" in model.last_call["user"]
    assert "Filas totales: 1" in model.last_call["user"]


def test_summarize_answer_handles_empty_dataframe():
    df = pd.DataFrame(columns=["total"])
    model = FakeChatModel("No hay resultados.")

    summarize_answer("pregunta", "SELECT * FROM t WHERE 1=0", df, model)

    assert "no devolvio filas" in model.last_call["user"]


def test_summarize_answer_truncates_large_results():
    df = pd.DataFrame({"n": range(100)})
    model = FakeChatModel("respuesta")

    summarize_answer("pregunta", "SELECT n FROM t", df, model)

    assert "70 filas mas" in model.last_call["user"]
