from reporting_automation.secrets.secret_manager import SecretManagerClient


class FakePayload:
    def __init__(self, data: bytes):
        self.data = data


class FakeResponse:
    def __init__(self, data: bytes):
        self.payload = FakePayload(data)


class FakeSecretManagerServiceClient:
    def __init__(self, secrets: dict[str, bytes]):
        self._secrets = secrets
        self.last_request = None

    def access_secret_version(self, request):
        self.last_request = request
        secret_id = request["name"].split("/secrets/")[1].split("/versions/")[0]
        return FakeResponse(self._secrets[secret_id])


def test_get_secret_returns_decoded_string():
    client = FakeSecretManagerServiceClient({"internal-smtp": b"hello"})
    sm = SecretManagerClient(client, "test-project")

    assert sm.get_secret("internal-smtp") == "hello"
    assert client.last_request["name"] == "projects/test-project/secrets/internal-smtp/versions/latest"


def test_get_json_secret_parses_json():
    payload = b'{"host": "smtp.gmail.com", "port": 587}'
    client = FakeSecretManagerServiceClient({"internal-smtp": payload})
    sm = SecretManagerClient(client, "test-project")

    assert sm.get_json_secret("internal-smtp") == {"host": "smtp.gmail.com", "port": 587}


def test_get_secret_uses_requested_version():
    client = FakeSecretManagerServiceClient({"x": b"v1"})
    sm = SecretManagerClient(client, "p")

    sm.get_secret("x", version="3")

    assert client.last_request["name"] == "projects/p/secrets/x/versions/3"
