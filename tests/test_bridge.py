import pytest

from agent.api_client import ApiAuthError, ApiUnavailableError
from agent.constants import CONFIG_SERVER_URL, CONFIG_SETUP_SKIPPED
from agent.database import LocalDatabase
from agent.state import AgentState
from agent.ui.bridge import JsBridge
from tests.conftest import add_connection, insert_capture

SERVER_URL = "https://unicesulab.test"

PAYLOAD = {
    "name": "Coagulômetro",
    "port": "COM13",
    "baudrate": "57600",
    "bytesize": "8",
    "parity": "N",
    "stopbits": "1",
    "idle_timeout_seconds": "3",
    "rtscts": True,
    "agent_token": "abc",
}


class FakeClient:
    fail_with: Exception | None = None
    created: list[tuple[str, str]] = []

    def __init__(self, server_url: str, token: str) -> None:
        FakeClient.created.append((server_url, token))

    def ping(self) -> dict:
        if FakeClient.fail_with:
            raise FakeClient.fail_with
        return {"agent": {"name": "Bancada"}, "machine": {"name": "Coagulômetro"}}


@pytest.fixture
def bridge(local_db: LocalDatabase) -> JsBridge:
    FakeClient.fail_with = None
    FakeClient.created = []
    local_db.set_config(CONFIG_SERVER_URL, SERVER_URL)
    return JsBridge(local_db, AgentState(), client_factory=FakeClient)


def test_save_server_url_validates_and_persists(local_db: LocalDatabase) -> None:
    bridge = JsBridge(local_db, AgentState(), client_factory=FakeClient)
    local_db.set_config(CONFIG_SETUP_SKIPPED, "1")

    assert bridge.save_server_url("unicesulab.test")["ok"] is False

    result = bridge.save_server_url(" https://unicesulab.test/ ")

    assert result == {"ok": True}
    assert local_db.get_config(CONFIG_SERVER_URL) == SERVER_URL
    assert local_db.get_config(CONFIG_SETUP_SKIPPED) is None
    assert bridge._state.server_url_changed.is_set()


def test_save_connection_validates_token_and_persists(bridge: JsBridge) -> None:
    result = bridge.save_connection(PAYLOAD)

    assert result["ok"] is True
    connection = bridge._db.get_connection(result["id"])
    assert connection["name"] == "Coagulômetro"
    assert connection["agent_token"] == "abc"
    assert FakeClient.created == [(SERVER_URL, "abc")]

    connection_state = bridge._state.connection(result["id"])
    assert connection_state.snapshot()["machine"] == {"name": "Coagulômetro"}
    assert connection_state.config_changed.is_set()
    assert bridge._state.connections_changed.is_set()


def test_save_connection_reports_rejected_token(bridge: JsBridge) -> None:
    FakeClient.fail_with = ApiAuthError("401")

    result = bridge.save_connection(PAYLOAD)

    assert result["ok"] is False
    assert "Token recusado" in result["error"]
    assert bridge._db.list_connections() == []


def test_save_connection_can_skip_validation_when_server_is_down(bridge: JsBridge) -> None:
    FakeClient.fail_with = ApiUnavailableError("down")

    assert bridge.save_connection(PAYLOAD)["ok"] is False
    result = bridge.save_connection({**PAYLOAD, "skip_validation": True})

    assert result["ok"] is True
    assert len(bridge._db.list_connections()) == 1


def test_save_connection_reports_invalid_payload(bridge: JsBridge) -> None:
    result = bridge.save_connection({**PAYLOAD, "name": ""})

    assert result["ok"] is False
    assert "nome" in result["error"]
    assert FakeClient.created == []


def test_edit_keeps_token_when_blank(bridge: JsBridge) -> None:
    created = bridge.save_connection(PAYLOAD)

    result = bridge.save_connection(
        {**PAYLOAD, "id": created["id"], "name": "Novo nome", "agent_token": ""}
    )

    assert result == {"ok": True, "id": created["id"]}
    connection = bridge._db.get_connection(created["id"])
    assert connection["name"] == "Novo nome"
    assert connection["agent_token"] == "abc"


def test_delete_connection(bridge: JsBridge) -> None:
    connection_id = add_connection(bridge._db)
    insert_capture(bridge._db, connection_id, "A")
    bridge._state.connection(connection_id)

    assert bridge.delete_connection(connection_id) == {"ok": True}
    assert bridge._db.list_connections() == []
    assert bridge._db.list_recent_captures() == []
    assert connection_id not in bridge._state.connection_ids()
    assert bridge.delete_connection(connection_id)["ok"] is False


def test_get_state_lists_connections_with_status(bridge: JsBridge) -> None:
    connection_id = add_connection(bridge._db, name="Bancada 1")
    insert_capture(bridge._db, connection_id, "EXAME: TP")
    bridge._state.connection(connection_id).append_live_text("olá")

    state = bridge.get_state()

    assert state["is_server_configured"] is True
    assert state["serial_defaults"]["baudrate"] == 57600
    connection = state["connections"][0]
    assert connection["name"] == "Bancada 1"
    assert connection["has_token"] is True
    assert "agent_token" not in connection
    assert connection["live_text"] == "olá"
    assert connection["serial"]["port"] == "COM13"
    assert state["recent_captures"][0]["preview"] == "TP"
    assert state["recent_captures"][0]["connection_name"] == "Bancada 1"


def test_detect_requires_enabled_connection(bridge: JsBridge) -> None:
    enabled = add_connection(bridge._db, port="COM1")
    disabled = add_connection(bridge._db, port="COM2", enabled=False)

    assert bridge.detect_baudrate(enabled) == {"ok": True}
    assert bridge._state.connection(enabled).detect_requested.is_set()
    assert bridge.detect_baudrate(disabled)["ok"] is False
    assert bridge.detect_baudrate(999)["ok"] is False
