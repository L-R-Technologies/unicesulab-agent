import pytest

from agent import database as db_status
from agent.api_client import ApiAuthError, ApiRejectedError, ApiUnavailableError
from agent.constants import AUTH_ERROR_RETRY_SECONDS, CONFIG_SERVER_URL
from agent.database import LocalDatabase
from agent.state import (
    SERVER_AUTH_ERROR,
    SERVER_NOT_CONFIGURED,
    SERVER_OFFLINE,
    SERVER_ONLINE,
    AgentState,
)
from agent.upload_worker import UploadWorker
from tests.conftest import FakeClock, add_connection, insert_capture

SERVER_URL = "https://unicesulab.test"


class FakeApiClient:
    def __init__(self, server_url: str, token: str) -> None:
        self.server_url = server_url
        self.token = token
        self.sent: list[str] = []
        self.pings = 0
        self.fail_with: Exception | None = None
        self.duplicate = False

    def ping(self) -> dict:
        self.pings += 1
        if self.fail_with:
            raise self.fail_with
        return {
            "agent": {"name": f"Agente {self.token}"},
            "machine": {"id": 3, "name": f"Máquina {self.token}"},
        }

    def send_capture(
        self, raw_text: str, raw_hash: str, captured_at: str, serial_settings: dict
    ) -> dict:
        if self.fail_with:
            raise self.fail_with
        self.sent.append(raw_hash)
        return {"id": 100 + len(self.sent), "status": "received", "duplicate": self.duplicate}


class ClientRegistry:
    def __init__(self) -> None:
        self.clients: dict[str, FakeApiClient] = {}

    def __call__(self, server_url: str, token: str) -> FakeApiClient:
        client = FakeApiClient(server_url, token)
        self.clients[token] = client
        return client


@pytest.fixture
def registry() -> ClientRegistry:
    return ClientRegistry()


@pytest.fixture
def configured_db(local_db: LocalDatabase) -> LocalDatabase:
    local_db.set_config(CONFIG_SERVER_URL, SERVER_URL)
    return local_db


def make_worker(
    local_db: LocalDatabase, registry: ClientRegistry, clock: FakeClock
) -> UploadWorker:
    worker = UploadWorker(local_db, AgentState(), client_factory=registry, clock=clock)
    worker._wait = lambda seconds: None
    return worker


def test_each_connection_sends_with_its_own_token(
    configured_db: LocalDatabase, registry: ClientRegistry, clock: FakeClock
) -> None:
    first = add_connection(configured_db, name="A", port="COM1", token="token-a")
    second = add_connection(configured_db, name="B", port="COM2", token="token-b")
    insert_capture(configured_db, first, "A1")
    insert_capture(configured_db, second, "B1")
    insert_capture(configured_db, first, "A2")
    worker = make_worker(configured_db, registry, clock)

    worker._run_cycle()

    assert registry.clients["token-a"].sent == ["hash-A1", "hash-A2"]
    assert registry.clients["token-b"].sent == ["hash-B1"]
    assert registry.clients["token-a"].server_url == SERVER_URL
    assert all(
        capture["status"] == db_status.CAPTURE_STATUS_SENT
        for capture in configured_db.list_recent_captures()
    )
    first_state = worker._state.connection(first).snapshot()
    assert first_state["server_status"] == SERVER_ONLINE
    assert first_state["machine"]["name"] == "Máquina token-a"
    assert worker._state.connection(second).snapshot()["agent_name"] == "Agente token-b"


def test_heartbeat_respects_interval_per_connection(
    configured_db: LocalDatabase, registry: ClientRegistry, clock: FakeClock
) -> None:
    add_connection(configured_db, token="token-a")
    worker = make_worker(configured_db, registry, clock)

    worker._run_cycle()
    worker._run_cycle()
    assert registry.clients["token-a"].pings == 1

    clock.advance(31)
    worker._run_cycle()
    assert registry.clients["token-a"].pings == 2


def test_token_change_recreates_client(
    configured_db: LocalDatabase, registry: ClientRegistry, clock: FakeClock
) -> None:
    connection_id = add_connection(configured_db, token="old")
    worker = make_worker(configured_db, registry, clock)
    worker._run_cycle()

    fields = dict(configured_db.get_connection(connection_id))
    fields["agent_token"] = "new"
    configured_db.update_connection(connection_id, fields)
    worker._run_cycle()

    assert set(registry.clients) == {"old", "new"}
    assert registry.clients["new"].pings == 1


def test_auth_error_in_one_connection_does_not_stop_the_other(
    configured_db: LocalDatabase, registry: ClientRegistry, clock: FakeClock
) -> None:
    bad = add_connection(configured_db, name="Ruim", port="COM1", token="bad")
    good = add_connection(configured_db, name="Boa", port="COM2", token="good")
    insert_capture(configured_db, bad, "X")
    insert_capture(configured_db, good, "Y")
    worker = make_worker(configured_db, registry, clock)
    worker._client_for(SERVER_URL, worker._enabled_connections()[0]).fail_with = ApiAuthError(
        "token inválido"
    )

    worker._run_cycle()

    assert worker._state.connection(bad).snapshot()["server_status"] == SERVER_AUTH_ERROR
    assert worker._state.connection(good).snapshot()["server_status"] == SERVER_ONLINE
    assert registry.clients["good"].sent == ["hash-Y"]
    assert (
        configured_db.list_pending_captures(bad)[0]["status"] == db_status.CAPTURE_STATUS_CAPTURED
    )

    worker._run_cycle()
    assert registry.clients["bad"].pings == 1

    clock.advance(AUTH_ERROR_RETRY_SECONDS + 1)
    worker._run_cycle()
    assert registry.clients["bad"].pings == 2


def test_forced_heartbeat_retries_after_auth_error(
    configured_db: LocalDatabase, registry: ClientRegistry, clock: FakeClock
) -> None:
    bad = add_connection(configured_db, token="bad")
    worker = make_worker(configured_db, registry, clock)
    worker._client_for(SERVER_URL, worker._enabled_connections()[0]).fail_with = ApiAuthError("x")
    worker._run_cycle()

    registry.clients["bad"].fail_with = None
    worker._state.connection(bad).heartbeat_requested.set()
    worker._run_cycle()

    assert worker._state.connection(bad).snapshot()["server_status"] == SERVER_ONLINE


def test_duplicate_counts_as_sent(
    configured_db: LocalDatabase, registry: ClientRegistry, clock: FakeClock
) -> None:
    connection_id = add_connection(configured_db, token="token-a")
    capture_id = insert_capture(configured_db, connection_id, "A")
    worker = make_worker(configured_db, registry, clock)
    worker._client_for(SERVER_URL, worker._enabled_connections()[0]).duplicate = True

    worker._run_cycle()

    assert configured_db.get_capture(capture_id)["status"] == db_status.CAPTURE_STATUS_SENT


def test_rejected_capture_is_marked_failed_and_others_continue(
    configured_db: LocalDatabase, registry: ClientRegistry, clock: FakeClock
) -> None:
    connection_id = add_connection(configured_db, token="token-a")
    rejected = insert_capture(configured_db, connection_id, "A")
    insert_capture(configured_db, connection_id, "B")

    class RejectFirst(FakeApiClient):
        def send_capture(self, raw_text, raw_hash, captured_at, serial_settings):
            if raw_hash == "hash-A":
                raise ApiRejectedError("Requisição recusada (HTTP 422): texto vazio")
            return super().send_capture(raw_text, raw_hash, captured_at, serial_settings)

    worker = UploadWorker(configured_db, AgentState(), client_factory=RejectFirst, clock=clock)
    worker._wait = lambda seconds: None
    worker._run_cycle()

    failed = configured_db.get_capture(rejected)
    assert failed["status"] == db_status.CAPTURE_STATUS_FAILED
    assert "422" in failed["error_message"]
    assert configured_db.get_capture(rejected + 1)["status"] == db_status.CAPTURE_STATUS_SENT


def test_server_unavailable_marks_all_connections_offline(
    configured_db: LocalDatabase, registry: ClientRegistry, clock: FakeClock
) -> None:
    first = add_connection(configured_db, name="A", port="COM1", token="a")
    second = add_connection(configured_db, name="B", port="COM2", token="b")
    capture_id = insert_capture(configured_db, first, "A")
    worker = make_worker(configured_db, registry, clock)
    worker._client_for(
        SERVER_URL, worker._enabled_connections()[0]
    ).fail_with = ApiUnavailableError("timeout")

    worker._run_cycle()

    assert configured_db.get_capture(capture_id)["status"] == db_status.CAPTURE_STATUS_CAPTURED
    assert worker._state.connection(first).snapshot()["server_status"] == SERVER_OFFLINE
    assert worker._state.connection(second).snapshot()["server_status"] == SERVER_OFFLINE
    assert worker._consecutive_server_failures == 1
    assert "b" not in registry.clients


def test_not_configured_without_server_url(
    local_db: LocalDatabase, registry: ClientRegistry, clock: FakeClock
) -> None:
    connection_id = add_connection(local_db)
    worker = make_worker(local_db, registry, clock)

    worker._run_cycle()

    assert worker._state.connection(connection_id).snapshot()["server_status"] == (
        SERVER_NOT_CONFIGURED
    )
    assert registry.clients == {}


def test_server_url_change_resets_clients(
    configured_db: LocalDatabase, registry: ClientRegistry, clock: FakeClock
) -> None:
    add_connection(configured_db, token="token-a")
    worker = make_worker(configured_db, registry, clock)
    worker._run_cycle()

    configured_db.set_config(CONFIG_SERVER_URL, "https://outro.test")
    worker._state.server_url_changed.set()
    worker._run_cycle()

    assert registry.clients["token-a"].server_url == "https://outro.test"
    assert registry.clients["token-a"].pings == 1
