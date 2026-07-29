import pytest

from reporting_automation.llm.anthropic_client import resolve_anthropic_api_key


class FakeSecretManager:
    def __init__(self, secrets: dict[str, str]):
        self._secrets = secrets

    def get_secret(self, secret_id: str, version: str = "latest") -> str:
        return self._secrets[secret_id]


class FailingSecretManager:
    def get_secret(self, secret_id: str, version: str = "latest") -> str:
        raise RuntimeError("secret not found")


def test_resolve_prefers_env_var(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    secret_manager = FakeSecretManager({"anthropic-api-key": "sk-from-secret"})
    assert resolve_anthropic_api_key(secret_manager) == "sk-from-env"


def test_resolve_falls_back_to_secret_manager(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    key = resolve_anthropic_api_key(FakeSecretManager({"anthropic-api-key": "sk-from-secret"}))
    assert key == "sk-from-secret"


def test_resolve_raises_clear_error_when_nothing_available(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        resolve_anthropic_api_key(FailingSecretManager())


def test_resolve_raises_when_no_secret_manager_and_no_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        resolve_anthropic_api_key(None)
