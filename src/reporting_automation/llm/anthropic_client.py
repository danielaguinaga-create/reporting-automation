from __future__ import annotations

import os

from reporting_automation.secrets.secret_manager import SecretManagerClient

DEFAULT_MODEL = "claude-sonnet-5"

_SECRET_ID = "anthropic-api-key"


def resolve_anthropic_api_key(secret_manager: SecretManagerClient | None = None) -> str:
    """Busca la API key en `ANTHROPIC_API_KEY` primero, y si no esta, en Secret
    Manager (secreto `anthropic-api-key`) -- misma convencion que `internal-smtp`
    para SMTP. Nunca hardcodeada en el repo."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key

    if secret_manager is not None:
        try:
            return secret_manager.get_secret(_SECRET_ID)
        except Exception:
            pass

    raise RuntimeError(
        "No se encontro una API key de Anthropic. Configura la variable de entorno "
        "ANTHROPIC_API_KEY, o guarda el secreto en Secret Manager:\n"
        f"  echo -n 'sk-ant-...' | gcloud secrets create {_SECRET_ID} --data-file=- "
        "--project=<tu-proyecto-gcp>"
    )


class AnthropicChatModel:
    """Envoltorio delgado sobre el SDK de Anthropic -- implementa el Protocol `ChatModel`
    de `sql_generator.py`/`nl_answer.py` sin que esos modulos dependan del SDK."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, max_tokens: int = 4096) -> None:
        import anthropic  # import perezoso: no requerido para tests que inyectan un fake

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def generate(self, *, system: str, user: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in response.content if block.type == "text")
