import pytest

from reporting_automation.config.client_registry import ClientRegistry
from reporting_automation.exceptions import ClientConfigError


def test_load_discovers_clients(clients_fixtures_dir):
    registry = ClientRegistry()
    registry.load(clients_fixtures_dir)

    ids = {c.id for c in registry.list_all()}
    assert ids == {"protec", "avanza_seguros"}


def test_get_or_none_returns_config(clients_fixtures_dir):
    registry = ClientRegistry()
    registry.load(clients_fixtures_dir)

    client = registry.get_or_none("protec")
    assert client is not None
    assert client.display_name == "Protec"
    assert client.bq_params == {"id_company": "498cb81c5ba7325f"}


def test_get_or_none_unknown_client_returns_none(clients_fixtures_dir):
    registry = ClientRegistry()
    registry.load(clients_fixtures_dir)

    assert registry.get_or_none("no_existe") is None


def test_load_tolerates_missing_directory(tmp_path):
    registry = ClientRegistry()
    registry.load(tmp_path / "does_not_exist")

    assert registry.list_all() == []
    assert registry.get_or_none("anything") is None


def test_duplicate_client_id_raises(tmp_path):
    body = "id: dup\ndisplay_name: Dup\nbq_params: {}\n"
    (tmp_path / "a.yaml").write_text(body)
    (tmp_path / "b.yaml").write_text(body)

    registry = ClientRegistry()
    with pytest.raises(ClientConfigError, match="duplicado"):
        registry.load(tmp_path)


def test_invalid_client_config_raises(tmp_path):
    (tmp_path / "broken.yaml").write_text("display_name: MissingId\n")

    registry = ClientRegistry()
    with pytest.raises(ClientConfigError, match="invalida"):
        registry.load(tmp_path)
