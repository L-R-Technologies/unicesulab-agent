from pathlib import Path

import pytest

from agent import database as db_status
from agent.api_client import ApiAuthError, ApiRejectedError, ApiUnavailableError
from agent.constants import CONFIG_AGENT_TOKEN, CONFIG_SERVER_URL
from agent.database import LocalDatabase
from agent.state import (
    SERVER_NOT_PAIRED,
    SERVER_OFFLINE,
    SERVER_ONLINE,
    AgentState,
)
from agent.upload_worker import UploadWorker


class FakeApiClient:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.pings = 0
        self.fail_with: Exception | None = None
        self.duplicate = False

    def ping(self) -> dict:
        self.pings += 1
        if self.fail_with:
            raise self.fail_with
        return {"agent": {"name": "Bancada 1"}, "machine": {"id": 3, "name": "Coagulômetro"}}

    def send_capture(
        self, raw_text: str, raw_hash: str, captured_at: str, serial_settings: dict
    ) -> dict:
        if self.fail_with:
            raise self.fail_with
        self.sent.append(raw_hash)
        return {"id": 100 + len(self.sent), "status": "received", "duplicate": self.duplicate}


@pytest.fixture
def local_db(tmp_path: Path) -> LocalDatabase:
    db = LocalDatabase(tmp_path / "test.db")
    db.set_config(CONFIG_SERVER_URL, "https://unicesulab.test")
    db.set_config(CONFIG_AGENT_TOKEN, "token")
    return db


@pytest.fixture
def worker(local_db: LocalDatabase) -> UploadWorker:
    instance = UploadWorker(local_db, AgentState())
    instance._api = FakeApiClient()
    instance._api_config = ("https://unicesulab.test", "token")
    instance._wait = lambda seconds: None
    return instance


def _insert(local_db: LocalDatabase, text: str) -> int:
    return local_db.insert_capture(
        text, text.encode().hex(), f"hash-{text}", "2026-10-28T10:00:00-03:00"
    )


def test_sends_pending_captures_in_order(worker: UploadWorker) -> None:
    first = _insert(worker._db, "A")
    second = _insert(worker._db, "B")

    worker._run_cycle()

    assert worker._api.sent == ["hash-A", "hash-B"]
    assert worker._db.get_capture(first)["status"] == db_status.CAPTURE_STATUS_SENT
    assert worker._db.get_capture(first)["server_capture_id"] == 101
    assert worker._db.get_capture(second)["server_capture_id"] == 102
    assert worker._state.snapshot()["server_status"] == SERVER_ONLINE


def test_heartbeat_stores_machine_info(worker: UploadWorker) -> None:
    worker._run_cycle()

    snapshot = worker._state.snapshot()
    assert worker._api.pings == 1
    assert snapshot["agent_name"] == "Bancada 1"
    assert snapshot["machine"]["name"] == "Coagulômetro"

    # Dentro do intervalo não repete o heartbeat
    worker._run_cycle()
    assert worker._api.pings == 1


def test_duplicate_counts_as_sent(worker: UploadWorker) -> None:
    capture_id = _insert(worker._db, "A")
    worker._api.duplicate = True

    worker._run_cycle()

    assert worker._db.get_capture(capture_id)["status"] == db_status.CAPTURE_STATUS_SENT


def test_rejected_capture_is_marked_failed_and_others_continue(worker: UploadWorker) -> None:
    rejected = _insert(worker._db, "A")
    _insert(worker._db, "B")

    class RejectFirst(FakeApiClient):
        def send_capture(self, raw_text, raw_hash, captured_at, serial_settings):
            if raw_hash == "hash-A":
                raise ApiRejectedError("Requisição recusada (HTTP 422): texto vazio")
            return super().send_capture(raw_text, raw_hash, captured_at, serial_settings)

    worker._api = RejectFirst()
    worker._run_cycle()

    failed = worker._db.get_capture(rejected)
    assert failed["status"] == db_status.CAPTURE_STATUS_FAILED
    assert "422" in failed["error_message"]
    assert worker._api.sent == ["hash-B"]


def test_server_unavailable_keeps_capture_pending(worker: UploadWorker) -> None:
    capture_id = _insert(worker._db, "A")
    worker._api.fail_with = ApiUnavailableError("timeout")

    worker._run_cycle()

    capture = worker._db.get_capture(capture_id)
    assert capture["status"] == db_status.CAPTURE_STATUS_CAPTURED
    assert worker._state.snapshot()["server_status"] == SERVER_OFFLINE
    assert worker._consecutive_server_failures == 1


def test_auth_error_sets_status_and_stops_sending(worker: UploadWorker) -> None:
    _insert(worker._db, "A")
    worker._api.fail_with = ApiAuthError("token inválido")

    # O ciclo propaga o erro de autenticação; é o run() que o converte em status
    with pytest.raises(ApiAuthError):
        worker._run_cycle()

    assert worker._db.list_pending_captures()[0]["status"] == db_status.CAPTURE_STATUS_CAPTURED


def test_not_paired_when_config_missing(tmp_path: Path) -> None:
    worker = UploadWorker(LocalDatabase(tmp_path / "empty.db"), AgentState())
    worker._wait = lambda seconds: None

    worker._run_cycle()

    assert worker._state.snapshot()["server_status"] == SERVER_NOT_PAIRED
