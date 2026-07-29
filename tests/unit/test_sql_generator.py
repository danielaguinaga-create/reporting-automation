import pytest

from reporting_automation.llm.sql_generator import GeneratedSql, SqlGenerationError, generate_sql


class FakeChatModel:
    def __init__(self, response: str):
        self._response = response
        self.last_call: dict | None = None

    def generate(self, *, system: str, user: str) -> str:
        self.last_call = {"system": system, "user": user}
        return self._response


def test_generate_sql_parses_sql_block_and_explanation():
    response = (
        "```sql\nSELECT COUNT(*) AS total FROM `proj.ds.usuarios`\n```\n\n"
        "Esta consulta cuenta el total de usuarios registrados."
    )
    model = FakeChatModel(response)

    result = generate_sql("cuantos usuarios hay", "- usuarios(UserToken STRING)", model, "proj", "ds")

    assert isinstance(result, GeneratedSql)
    assert result.sql == "SELECT COUNT(*) AS total FROM `proj.ds.usuarios`"
    assert "cuenta el total de usuarios" in result.explanation


def test_generate_sql_includes_schema_and_project_dataset_in_system_prompt():
    model = FakeChatModel("```sql\nSELECT 1\n```")
    generate_sql("pregunta", "- tabla(col STRING)", model, "myproject", "mydataset")

    assert "myproject" in model.last_call["system"]
    assert "mydataset" in model.last_call["system"]
    assert "tabla(col STRING)" in model.last_call["system"]
    assert "pregunta" in model.last_call["user"]


def test_generate_sql_raises_when_no_sql_block_present():
    model = FakeChatModel("No puedo responder esa pregunta con los datos disponibles.")

    with pytest.raises(SqlGenerationError):
        generate_sql("pregunta rara", "- tabla(col STRING)", model, "proj", "ds")
