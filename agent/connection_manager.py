import logging
import threading
from collections.abc import Callable

from agent.capture_worker import CaptureWorker
from agent.constants import CONNECTION_RECONCILE_INTERVAL_SECONDS
from agent.database import LocalDatabase
from agent.state import SERIAL_DISABLED, AgentState

logger = logging.getLogger(__name__)

WorkerFactory = Callable[[int], CaptureWorker]

_SIGNATURE_FIELDS = (
    "port",
    "baudrate",
    "bytesize",
    "parity",
    "stopbits",
    "rtscts",
    "idle_timeout_seconds",
)


class ConnectionManager(threading.Thread):
    def __init__(
        self,
        local_db: LocalDatabase,
        state: AgentState,
        worker_factory: WorkerFactory | None = None,
    ) -> None:
        super().__init__(daemon=True, name="connection-manager")
        self._db = local_db
        self._state = state
        self._worker_factory = worker_factory or self._default_worker
        self._workers: dict[int, CaptureWorker] = {}
        self._signatures: dict[int, tuple] = {}
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()
        self._state.connections_changed.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.reconcile()
            except Exception:
                logger.exception("Erro inesperado ao reconciliar as conexões")
            self._state.connections_changed.wait(CONNECTION_RECONCILE_INTERVAL_SECONDS)
            self._state.connections_changed.clear()
        self._stop_all()

    def running_connection_ids(self) -> list[int]:
        return sorted(self._workers)

    def reconcile(self) -> None:
        rows = {int(row["id"]): row for row in self._db.list_connections()}

        for connection_id in list(self._workers):
            row = rows.get(connection_id)
            if row is None or not row["enabled"]:
                self._stop_worker(connection_id)
            if row is None:
                self._state.remove_connection(connection_id)

        for connection_id, row in rows.items():
            if not row["enabled"]:
                self._state.connection(connection_id).set_serial_status(SERIAL_DISABLED)
                continue
            self._ensure_worker(connection_id, _signature_of(row))

    def _ensure_worker(self, connection_id: int, signature: tuple) -> None:
        worker = self._workers.get(connection_id)
        if worker is None or not worker.is_alive():
            self._start_worker(connection_id)
        elif self._signatures.get(connection_id) != signature:
            logger.info("Configuração da conexão %d mudou; reabrindo a porta", connection_id)
            self._state.connection(connection_id).config_changed.set()
        self._signatures[connection_id] = signature

    def _start_worker(self, connection_id: int) -> None:
        worker = self._worker_factory(connection_id)
        worker.start()
        self._workers[connection_id] = worker
        logger.info("Conexão %d iniciada", connection_id)

    def _stop_worker(self, connection_id: int) -> None:
        worker = self._workers.pop(connection_id, None)
        self._signatures.pop(connection_id, None)
        if worker is not None:
            worker.stop()
            logger.info("Conexão %d parada", connection_id)

    def _stop_all(self) -> None:
        for connection_id in list(self._workers):
            self._stop_worker(connection_id)

    def _default_worker(self, connection_id: int) -> CaptureWorker:
        return CaptureWorker(self._db, self._state, connection_id)


def _signature_of(row: dict) -> tuple:
    return tuple(row[field] for field in _SIGNATURE_FIELDS)
