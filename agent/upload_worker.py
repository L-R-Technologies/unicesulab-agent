"""Loop de envio: heartbeat no servidor e envio das capturas pendentes."""

import logging
import threading
import time

from agent.api_client import (
    ApiAuthError,
    ApiClient,
    ApiError,
    ApiRejectedError,
    ApiUnavailableError,
)
from agent.constants import (
    AUTH_ERROR_RETRY_SECONDS,
    CONFIG_AGENT_TOKEN,
    CONFIG_SERVER_URL,
    HEARTBEAT_INTERVAL_SECONDS,
    UPLOAD_BACKOFF_STEPS_SECONDS,
    UPLOAD_INTERVAL_SECONDS,
)
from agent.database import LocalDatabase
from agent.serial_settings import SerialSettings
from agent.state import (
    SERVER_AUTH_ERROR,
    SERVER_NOT_PAIRED,
    SERVER_OFFLINE,
    SERVER_ONLINE,
    AgentState,
)

logger = logging.getLogger(__name__)


class UploadWorker(threading.Thread):
    def __init__(self, local_db: LocalDatabase, state: AgentState) -> None:
        super().__init__(daemon=True, name="upload-worker")
        self._db = local_db
        self._state = state
        self._stop_event = threading.Event()
        self._api: ApiClient | None = None
        self._api_config: tuple | None = None
        self._consecutive_server_failures = 0
        self._last_heartbeat_at = 0.0

    def stop(self) -> None:
        self._stop_event.set()
        self._state.upload_requested.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_cycle()
            except ApiAuthError as exc:
                logger.warning("Autenticação recusada pelo servidor: %s", exc)
                self._state.set_server_status(SERVER_AUTH_ERROR, error_message=str(exc))
                self._wait(AUTH_ERROR_RETRY_SECONDS)
            except Exception:
                logger.exception("Erro inesperado no ciclo de envio")
                self._wait(UPLOAD_INTERVAL_SECONDS)

    def _wait(self, seconds: float) -> None:
        # Acorda antes do prazo se chegar captura nova ou o operador pedir
        self._state.upload_requested.wait(seconds)
        self._state.upload_requested.clear()

    # --- Ciclo principal ---

    def _run_cycle(self) -> None:
        if not self._refresh_api_client():
            self._state.set_server_status(SERVER_NOT_PAIRED)
            self._wait(UPLOAD_INTERVAL_SECONDS)
            return

        try:
            self._run_heartbeat_if_due()
            self._send_pending_captures()
        except ApiUnavailableError as exc:
            self._register_server_failure(exc)
            return

        self._wait(UPLOAD_INTERVAL_SECONDS)

    def _refresh_api_client(self) -> bool:
        server_url = self._db.get_config(CONFIG_SERVER_URL)
        token = self._db.get_config(CONFIG_AGENT_TOKEN)

        if not server_url or not token:
            self._api = None
            self._api_config = None
            return False

        current_config = (server_url, token)
        if current_config != self._api_config or self._state.pairing_changed.is_set():
            self._api = ApiClient(server_url, token)
            self._api_config = current_config
            self._last_heartbeat_at = 0.0
            self._state.pairing_changed.clear()
        return True

    # --- Heartbeat ---

    def _run_heartbeat_if_due(self) -> None:
        forced = self._state.heartbeat_requested.is_set()
        elapsed = time.monotonic() - self._last_heartbeat_at
        if not forced and elapsed < HEARTBEAT_INTERVAL_SECONDS:
            return

        self._state.heartbeat_requested.clear()
        self._last_heartbeat_at = time.monotonic()

        info = self._api.ping()
        agent_info = info.get("agent") or {}
        self._state.set_pairing_info(agent_info.get("name"), info.get("machine"))
        self._mark_server_online()

    # --- Envio ---

    def _send_pending_captures(self) -> None:
        serial_settings = SerialSettings.load(self._db).to_api_payload()

        for capture in self._db.list_pending_captures():
            if self._stop_event.is_set():
                return
            self._send_capture(capture, serial_settings)

    def _send_capture(self, capture: dict, serial_settings: dict) -> None:
        attempts = int(capture["attempts"]) + 1
        try:
            result = self._api.send_capture(
                capture["raw_text"], capture["raw_hash"], capture["captured_at"], serial_settings
            )
        except ApiRejectedError as exc:
            logger.warning("Captura #%d recusada pelo servidor: %s", capture["id"], exc)
            self._db.mark_capture_failed(capture["id"], attempts, str(exc))
            return
        except ApiUnavailableError as exc:
            self._db.mark_capture_attempt(capture["id"], attempts, str(exc))
            raise
        except ApiError as exc:
            # Resposta inesperada (ex.: JSON inválido): registra e tenta no próximo ciclo
            logger.warning("Resposta inválida ao enviar captura #%d: %s", capture["id"], exc)
            self._db.mark_capture_attempt(capture["id"], attempts, str(exc))
            return

        server_id = int(result.get("id", 0)) or None
        self._db.mark_capture_sent(capture["id"], server_id, attempts)
        self._mark_server_online()
        logger.info(
            "Captura #%d enviada (id no servidor: %s%s)",
            capture["id"],
            server_id,
            ", duplicada" if result.get("duplicate") else "",
        )

    def _mark_server_online(self) -> None:
        self._consecutive_server_failures = 0
        self._state.set_server_status(SERVER_ONLINE)

    def _register_server_failure(self, exc: ApiUnavailableError) -> None:
        self._consecutive_server_failures += 1
        self._state.set_server_status(SERVER_OFFLINE, error_message=str(exc))
        backoff_index = min(
            self._consecutive_server_failures - 1,
            len(UPLOAD_BACKOFF_STEPS_SECONDS) - 1,
        )
        self._wait(UPLOAD_BACKOFF_STEPS_SECONDS[backoff_index])
