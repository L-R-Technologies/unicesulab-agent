"""Loop da porta serial: abre com retry, escuta, fecha capturas e guarda na fila."""

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime

from agent.baud_detector import detect_baudrate
from agent.constants import (
    SERIAL_NOT_CONFIGURED_WAIT_SECONDS,
    SERIAL_OPEN_BACKOFF_STEPS_SECONDS,
)
from agent.database import LocalDatabase
from agent.serial_reader import (
    ByteSource,
    CaptureFramer,
    SerialOpenError,
    SerialReadError,
    build_capture,
    decode_live_text,
    open_serial_port,
)
from agent.serial_settings import SerialSettings
from agent.state import (
    SERIAL_CONNECTING,
    SERIAL_DETECTING,
    SERIAL_ERROR,
    SERIAL_LISTENING,
    SERIAL_NOT_CONFIGURED,
    AgentState,
)

logger = logging.getLogger(__name__)

SourceFactory = Callable[[SerialSettings], ByteSource]


class CaptureWorker(threading.Thread):
    def __init__(
        self,
        local_db: LocalDatabase,
        state: AgentState,
        source_factory: SourceFactory = open_serial_port,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(daemon=True, name="capture-worker")
        self._db = local_db
        self._state = state
        self._source_factory = source_factory
        self._clock = clock
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_cycle()
            except Exception:
                logger.exception("Erro inesperado no ciclo da porta serial")
                self._state.set_serial_status(SERIAL_ERROR, "Erro inesperado. Veja os logs.")
                self._wait(SERIAL_NOT_CONFIGURED_WAIT_SECONDS)

    def _wait(self, seconds: float) -> None:
        self._stop_event.wait(seconds)

    def _should_reopen(self) -> bool:
        return (
            self._stop_event.is_set()
            or self._state.serial_config_changed.is_set()
            or self._state.detect_requested.is_set()
        )

    # --- Ciclo principal ---

    def _run_cycle(self) -> None:
        self._state.serial_config_changed.clear()
        settings = SerialSettings.load(self._db)

        if not settings.is_configured:
            self._state.set_serial_status(SERIAL_NOT_CONFIGURED)
            self._state.detect_requested.clear()
            self._wait(SERIAL_NOT_CONFIGURED_WAIT_SECONDS)
            return

        if self._state.detect_requested.is_set():
            self._run_detection(settings)
            return

        source = self._open_with_retry(settings)
        if source is None:
            return

        self._state.set_serial_status(SERIAL_LISTENING)
        try:
            self._listen(source, settings)
        finally:
            source.close()

    def _open_with_retry(self, settings: SerialSettings) -> ByteSource | None:
        """Insiste na abertura até conseguir, parar ou a configuração mudar."""
        attempt = 0
        while not self._should_reopen():
            self._state.set_serial_status(SERIAL_CONNECTING)
            try:
                source = self._source_factory(settings)
                logger.info("Porta %s aberta a %d baud", settings.port, settings.baudrate)
                return source
            except SerialOpenError as exc:
                attempt += 1
                logger.warning("Tentativa %d de abrir %s falhou: %s", attempt, settings.port, exc)
                self._state.set_serial_status(SERIAL_ERROR, str(exc))
                backoff_index = min(attempt - 1, len(SERIAL_OPEN_BACKOFF_STEPS_SECONDS) - 1)
                self._wait(SERIAL_OPEN_BACKOFF_STEPS_SECONDS[backoff_index])
        return None

    def _listen(self, source: ByteSource, settings: SerialSettings) -> None:
        framer = CaptureFramer(settings.idle_timeout_seconds, clock=self._clock)

        while not self._should_reopen():
            try:
                data = source.read_available()
            except SerialReadError as exc:
                logger.warning("Leitura da porta %s falhou: %s", settings.port, exc)
                self._state.set_serial_status(SERIAL_ERROR, str(exc))
                self._store_capture(framer.flush())
                return

            if data:
                self._state.append_live_text(decode_live_text(data))
                for completed in framer.feed(data):
                    self._store_capture(completed)
            else:
                self._store_capture(framer.poll())

        # Ao sair (parada, reconfiguração ou detecção) nada fica perdido no buffer
        self._store_capture(framer.flush())

    def _store_capture(self, raw_bytes: bytes | None) -> None:
        if raw_bytes is None:
            return
        capture = build_capture(raw_bytes, datetime.now().astimezone())
        if capture is None:
            logger.info("Captura descartada: só bytes em branco (%d bytes)", len(raw_bytes))
            return

        capture_id = self._db.insert_capture(
            capture.text, capture.raw_bytes_hex, capture.raw_hash, capture.captured_at_iso
        )
        logger.info("Captura #%d guardada (%d bytes)", capture_id, len(raw_bytes))
        self._state.upload_requested.set()

    # --- Detecção de baudrate ---

    def _run_detection(self, settings: SerialSettings) -> None:
        self._state.detect_requested.clear()
        self._state.set_detect_running()
        self._state.set_serial_status(SERIAL_DETECTING)

        result = detect_baudrate(
            settings, self._source_factory, clock=self._clock, stop_event=self._stop_event
        )
        logger.info("Detecção de baudrate: %s", result.message)

        if result.best_baudrate is not None:
            settings.with_baudrate(result.best_baudrate).save(self._db)
        self._state.set_detect_result(result.to_dict())
