import pytest
import requests

from agent.api_client import (
    ApiAuthError,
    ApiClient,
    ApiError,
    ApiRejectedError,
    ApiUnavailableError,
)


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.headers: dict[str, str] = {}
        self._response = response
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **kwargs) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def make_client(response) -> tuple[ApiClient, FakeSession]:
    session = FakeSession(response)
    return ApiClient("https://unicesulab.test/", "secret", session=session), session


def test_ping_sends_bearer_token_and_returns_data() -> None:
    payload = {"data": {"agent": {"name": "A"}, "machine": None}}
    client, session = make_client(FakeResponse(200, payload))

    data = client.ping()

    assert data["agent"]["name"] == "A"
    assert session.headers["Authorization"] == "Bearer secret"
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("GET", "https://unicesulab.test/api/agent/ping")
    assert "agent_version" in kwargs["params"]


def test_send_capture_posts_payload() -> None:
    payload = {"data": {"id": 7, "status": "received", "duplicate": False}}
    client, session = make_client(FakeResponse(201, payload))

    data = client.send_capture("texto", "hash", "2026-10-28T10:00:00-03:00", {"port": "COM13"})

    assert data["id"] == 7
    body = session.calls[0][2]["json"]
    assert body["raw_text"] == "texto"
    assert body["serial_settings"] == {"port": "COM13"}


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (FakeResponse(401, {"message": "Token inválido"}), ApiAuthError),
        (FakeResponse(403, {"message": "Agente inativo"}), ApiAuthError),
        (FakeResponse(422, {"message": "raw_text obrigatório"}), ApiRejectedError),
        (FakeResponse(429, None, "slow down"), ApiUnavailableError),
        (FakeResponse(503, None, "down"), ApiUnavailableError),
        (requests.ConnectionError("refused"), ApiUnavailableError),
        (FakeResponse(200, None, "<html>"), ApiError),
        (FakeResponse(200, {"ok": True}), ApiError),
    ],
)
def test_error_mapping(response, expected) -> None:
    client, _ = make_client(response)

    with pytest.raises(expected):
        client.ping()
