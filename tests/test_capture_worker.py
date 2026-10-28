import pytest

from agent.capture_worker import CaptureWorker
from agent.database import LocalDatabase
from agent.serial_reader import SerialOpenError, SerialReadError
from agent.serial_settings import SerialSettings
from agent.state import SERIAL_DISABLED, SERIAL_ERROR, SERIAL_LISTENING, AgentState
from tests.conftest import FakeClock, add_connection

IDLE_TIMEOUT = 3.0
READ_TIMEOUT = 1.0


class ScriptedSource:
    def __init__(self, clock: FakeClock, chunks: list) -> None:
        self._clock = clock
        self._chunks = list(chunks)
        self.closed = False

    def read_available(self) -> bytes:
        self._clock.advance(READ_TIMEOUT)
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        if isinstance(chunk, Exception):
            raise chunk
        if callable(chunk):
            return chunk()
        return chunk

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def connection_id(local_db: LocalDatabase) -> int:
    return add_connection(local_db, idle_timeout_seconds=IDLE_TIMEOUT)


def make_worker(
    local_db: LocalDatabase, connection_id: int, clock: FakeClock, factory
) -> tuple[CaptureWorker, AgentState]:
    state = AgentState()
    worker = CaptureWorker(local_db, state, connection_id, source_factory=factory, clock=clock)
    worker._wait = lambda seconds: None
    return worker, state


def test_listen_stores_capture_after_silence(
    local_db: LocalDatabase, connection_id: int, clock: FakeClock
) -> None:
    source = ScriptedSource(clock, [b"UNICESUMAR\n\r", b"EXAME: TP\n\r", b"", b"", b"", b""])
    worker, state = make_worker(local_db, connection_id, clock, lambda settings: source)

    original_store = worker._store_capture

    def store_and_stop(raw: bytes | None) -> None:
        original_store(raw)
        if raw is not None:
            worker.stop()

    worker._store_capture = store_and_stop
    worker._run_cycle()

    pending = local_db.list_pending_captures()
    assert len(pending) == 1
    assert pending[0]["raw_text"] == "UNICESUMAR\nEXAME: TP"
    assert pending[0]["connection_id"] == connection_id
    assert state.upload_requested.is_set()
    assert "UNICESUMAR\nEXAME: TP\n" in state.connection(connection_id).snapshot()["live_text"]
    assert source.closed


def test_listen_flushes_buffer_when_config_changes(
    local_db: LocalDatabase, connection_id: int, clock: FakeClock
) -> None:
    state = AgentState()

    def change_config() -> bytes:
        state.connection(connection_id).config_changed.set()
        return b""

    source = ScriptedSource(clock, [b"PARCIAL", change_config])
    worker = CaptureWorker(
        local_db, state, connection_id, source_factory=lambda settings: source, clock=clock
    )
    worker._run_cycle()

    assert [capture["raw_text"] for capture in local_db.list_pending_captures()] == ["PARCIAL"]
    assert source.closed


def test_read_error_sets_error_status_and_keeps_partial_capture(
    local_db: LocalDatabase, connection_id: int, clock: FakeClock
) -> None:
    source = ScriptedSource(clock, [b"ANTES DO ERRO", SerialReadError("cabo removido")])
    worker, state = make_worker(local_db, connection_id, clock, lambda settings: source)

    worker._run_cycle()

    snapshot = state.connection(connection_id).snapshot()
    assert snapshot["serial_status"] == SERIAL_ERROR
    assert "cabo removido" in snapshot["serial_error_message"]
    pending = [capture["raw_text"] for capture in local_db.list_pending_captures()]
    assert pending == ["ANTES DO ERRO"]


def test_open_retries_until_it_succeeds(
    local_db: LocalDatabase, connection_id: int, clock: FakeClock
) -> None:
    attempts = {"count": 0}
    source = ScriptedSource(clock, [])

    def flaky_factory(settings: SerialSettings) -> ScriptedSource:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise SerialOpenError("Acesso negado")
        return source

    worker, state = make_worker(local_db, connection_id, clock, flaky_factory)
    worker._listen = lambda source, settings: worker.stop()
    worker._run_cycle()

    assert attempts["count"] == 3
    assert state.connection(connection_id).snapshot()["serial_status"] == SERIAL_LISTENING


def test_detection_saves_best_baudrate(
    local_db: LocalDatabase, connection_id: int, clock: FakeClock
) -> None:
    def factory(settings: SerialSettings) -> ScriptedSource:
        payload = b"EXAME: TP\n\r" * 10 if settings.baudrate == 9600 else b"\xff\x80" * 10
        return ScriptedSource(clock, [payload])

    worker, state = make_worker(local_db, connection_id, clock, factory)
    connection_state = state.connection(connection_id)
    connection_state.detect_requested.set()
    worker._run_cycle()

    assert local_db.get_connection(connection_id)["baudrate"] == 9600
    snapshot = connection_state.snapshot()
    assert snapshot["detect_status"] == "done"
    assert snapshot["detect_result"]["best_baudrate"] == 9600
    assert not connection_state.detect_requested.is_set()


def test_worker_stops_when_connection_is_disabled_or_removed(
    local_db: LocalDatabase, clock: FakeClock
) -> None:
    disabled_id = add_connection(local_db, enabled=False)
    worker, state = make_worker(
        local_db, disabled_id, clock, lambda settings: ScriptedSource(clock, [])
    )
    worker._run_cycle()
    assert state.connection(disabled_id).snapshot()["serial_status"] == SERIAL_DISABLED
    assert worker._stop_event.is_set()

    missing_worker, _ = make_worker(
        local_db, 999, clock, lambda settings: ScriptedSource(clock, [])
    )
    missing_worker._run_cycle()
    assert missing_worker._stop_event.is_set()
