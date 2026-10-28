from pathlib import Path

import pytest

from agent.capture_worker import CaptureWorker
from agent.database import LocalDatabase
from agent.serial_reader import SerialOpenError, SerialReadError
from agent.serial_settings import SerialSettings
from agent.state import SERIAL_ERROR, SERIAL_LISTENING, AgentState
from tests.conftest import FakeClock

IDLE_TIMEOUT = 3.0
READ_TIMEOUT = 1.0


class ScriptedSource:
    """Entrega os pedaços na ordem; b"" simula timeout sem dados.

    Um pedaço pode ser uma exceção (lançada na leitura) ou uma função
    chamada no momento da leitura (para disparar eventos no meio do loop).
    """

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
def local_db(tmp_path: Path) -> LocalDatabase:
    db = LocalDatabase(tmp_path / "test.db")
    SerialSettings(port="COM13", idle_timeout_seconds=IDLE_TIMEOUT).save(db)
    return db


def make_worker(
    local_db: LocalDatabase, clock: FakeClock, factory
) -> tuple[CaptureWorker, AgentState]:
    state = AgentState()
    worker = CaptureWorker(local_db, state, source_factory=factory, clock=clock)
    worker._wait = lambda seconds: None
    return worker, state


def test_listen_stores_capture_after_silence(local_db: LocalDatabase, clock: FakeClock) -> None:
    source = ScriptedSource(clock, [b"UNICESUMAR\n\r", b"EXAME: TP\n\r", b"", b"", b"", b""])
    worker, state = make_worker(local_db, clock, lambda settings: source)

    # Após a captura ser fechada, pede parada para o loop terminar
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
    assert state.upload_requested.is_set()
    assert "UNICESUMAR\nEXAME: TP\n" in state.snapshot()["live_text"]
    assert source.closed


def test_listen_flushes_buffer_when_config_changes(
    local_db: LocalDatabase, clock: FakeClock
) -> None:
    state = AgentState()

    def change_config() -> bytes:
        state.serial_config_changed.set()
        return b""

    source = ScriptedSource(clock, [b"PARCIAL", change_config])
    worker = CaptureWorker(local_db, state, source_factory=lambda settings: source, clock=clock)
    worker._run_cycle()

    assert [capture["raw_text"] for capture in local_db.list_pending_captures()] == ["PARCIAL"]
    assert source.closed


def test_read_error_sets_error_status_and_keeps_partial_capture(
    local_db: LocalDatabase, clock: FakeClock
) -> None:
    source = ScriptedSource(clock, [b"ANTES DO ERRO", SerialReadError("cabo removido")])
    worker, state = make_worker(local_db, clock, lambda settings: source)

    worker._run_cycle()

    assert state.snapshot()["serial_status"] == SERIAL_ERROR
    assert "cabo removido" in state.snapshot()["serial_error_message"]
    pending = [capture["raw_text"] for capture in local_db.list_pending_captures()]
    assert pending == ["ANTES DO ERRO"]


def test_open_retries_until_it_succeeds(local_db: LocalDatabase, clock: FakeClock) -> None:
    attempts = {"count": 0}
    source = ScriptedSource(clock, [])

    def flaky_factory(settings: SerialSettings) -> ScriptedSource:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise SerialOpenError("Acesso negado")
        return source

    worker, state = make_worker(local_db, clock, flaky_factory)
    worker._listen = lambda source, settings: worker.stop()
    worker._run_cycle()

    assert attempts["count"] == 3
    assert state.snapshot()["serial_status"] == SERIAL_LISTENING


def test_detection_saves_best_baudrate(local_db: LocalDatabase, clock: FakeClock) -> None:
    def factory(settings: SerialSettings) -> ScriptedSource:
        payload = b"EXAME: TP\n\r" * 10 if settings.baudrate == 9600 else b"\xff\x80" * 10
        return ScriptedSource(clock, [payload])

    worker, state = make_worker(local_db, clock, factory)
    state.detect_requested.set()
    worker._run_cycle()

    assert SerialSettings.load(local_db).baudrate == 9600
    snapshot = state.snapshot()
    assert snapshot["detect_status"] == "done"
    assert snapshot["detect_result"]["best_baudrate"] == 9600
    assert not state.detect_requested.is_set()


def test_worker_reports_not_configured_without_port(tmp_path: Path, clock: FakeClock) -> None:
    local_db = LocalDatabase(tmp_path / "empty.db")
    worker, state = make_worker(local_db, clock, lambda settings: ScriptedSource(clock, []))

    worker._run_cycle()

    assert state.snapshot()["serial_status"] == "not_configured"
