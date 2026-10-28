import logging
import threading
import time
from collections.abc import Callable

from agent.api_client import (
    ApiAuthError,
    ApiClient,
    ApiError,
    ApiRejectedError,
    ApiUnavailableError,
)
from agent.connection import Connection
from agent.constants import (
    AUTH_ERROR_RETRY_SECONDS,
    CONFIG_SERVER_URL,
    HEARTBEAT_INTERVAL_SECONDS,
    UPLOAD_BACKOFF_STEPS_SECONDS,
    UPLOAD_INTERVAL_SECONDS,
)
from agent.database import LocalDatabase
from agent.state import (
    SERVER_AUTH_ERROR,
    SERVER_NOT_CONFIGURED,
    SERVER_OFFLINE,
    SERVER_ONLINE,
    AgentState,
    ConnectionState,
)

logger = logging.getLogger(__name__)

ClientFactory = Callable[[str, str], ApiClient]


class UploadWorker(threading.Thread):
    def __init__(
        self,
        local_db: LocalDatabase,
        state: AgentState,
        client_factory: ClientFactory = ApiClient,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(daemon=True, name="upload-worker")
        self._db = local_db
        self._state = state
        self._client_factory = client_factory
        self._clock = clock
        self._stop_event = threading.Event()
        self._clients: dict[int, tuple[str, str, ApiClient]] = {}
        self._last_heartbeat_at: dict[int, float] = {}
        self._last_auth_error_at: dict[int, float] = {}
        self._consecutive_server_failures = 0

    def stop(self) -> None:
        self._stop_event.set()
        self._state.upload_requested.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_cycle()
            except Exception:
                logger.exception("Erro inesperado no ciclo de envio")
                self._wait(UPLOAD_INTERVAL_SECONDS)

    def _wait(self, seconds: float) -> None:
        self._state.upload_requested.wait(seconds)
        self._state.upload_requested.clear()

    def _run_cycle(self) -> None:
        server_url = self._db.get_config(CONFIG_SERVER_URL)
        connections = self._enabled_connections()

        if not server_url:
            for connection in connections:
                self._state.connection(connection.id).set_server_status(SERVER_NOT_CONFIGURED)
            self._wait(UPLOAD_INTERVAL_SECONDS)
            return

        if self._state.server_url_changed.is_set():
            self._state.server_url_changed.clear()
            self._clients.clear()
            self._last_heartbeat_at.clear()
            self._last_auth_error_at.clear()

        for connection in connections:
            if self._stop_event.is_set():
                return
            try:
                self._sync_connection(server_url, connection)
            except ApiAuthError as exc:
                self._register_auth_error(connection, exc)
            except ApiUnavailableError as exc:
                self._register_server_failure(connections, exc)
                return

        self._wait(UPLOAD_INTERVAL_SECONDS)

    def _enabled_connections(self) -> list[Connection]:
        return [
            Connection.from_row(row) for row in self._db.list_connections() if row["enabled"]
        ]

    def _sync_connection(self, server_url: str, connection: Connection) -> None:
        connection_state = self._state.connection(connection.id)
        if self._is_waiting_after_auth_error(connection, connection_state):
            return

        client = self._client_for(server_url, connection)
        self._run_heartbeat_if_due(connection, client, connection_state)
        self._send_pending_captures(connection, client, connection_state)

    def _client_for(self, server_url: str, connection: Connection) -> ApiClient:
        cached = self._clients.get(connection.id)
        if cached is not None and cached[0] == server_url and cached[1] == connection.agent_token:
            return cached[2]

        client = self._client_factory(server_url, connection.agent_token)
        self._clients[connection.id] = (server_url, connection.agent_token, client)
        self._last_heartbeat_at.pop(connection.id, None)
        self._last_auth_error_at.pop(connection.id, None)
        return client

    def _is_waiting_after_auth_error(
        self, connection: Connection, connection_state: ConnectionState
    ) -> bool:
        if connection_state.heartbeat_requested.is_set():
            return False
        if connection_state.server_status() != SERVER_AUTH_ERROR:
            return False
        failed_at = self._last_auth_error_at.get(connection.id)
        return failed_at is not None and self._clock() - failed_at < AUTH_ERROR_RETRY_SECONDS

    def _run_heartbeat_if_due(
        self, connection: Connection, client: ApiClient, connection_state: ConnectionState
    ) -> None:
        forced = connection_state.heartbeat_requested.is_set()
        last_at = self._last_heartbeat_at.get(connection.id)
        is_due = last_at is None or self._clock() - last_at >= HEARTBEAT_INTERVAL_SECONDS
        if not forced and not is_due:
            return

        connection_state.heartbeat_requested.clear()
        self._last_heartbeat_at[connection.id] = self._clock()

        info = client.ping()
        agent_info = info.get("agent") or {}
        connection_state.set_pairing_info(agent_info.get("name"), info.get("machine"))
        self._mark_online(connection_state)

    def _send_pending_captures(
        self, connection: Connection, client: ApiClient, connection_state: ConnectionState
    ) -> None:
        serial_settings = connection.serial.to_api_payload()

        for capture in self._db.list_pending_captures(connection.id):
            if self._stop_event.is_set():
                return
            self._send_capture(capture, client, serial_settings, connection_state)

    def _send_capture(
        self,
        capture: dict,
        client: ApiClient,
        serial_settings: dict,
        connection_state: ConnectionState,
    ) -> None:
        attempts = int(capture["attempts"]) + 1
        try:
            result = client.send_capture(
                capture["raw_text"], capture["raw_hash"], capture["captured_at"], serial_settings
            )
        except ApiRejectedError as exc:
            logger.warning("Captura %d recusada pelo servidor: %s", capture["id"], exc)
            self._db.mark_capture_failed(capture["id"], attempts, str(exc))
            return
        except (ApiUnavailableError, ApiAuthError) as exc:
            self._db.mark_capture_attempt(capture["id"], attempts, str(exc))
            raise
        except ApiError as exc:
            logger.warning("Resposta inválida ao enviar captura %d: %s", capture["id"], exc)
            self._db.mark_capture_attempt(capture["id"], attempts, str(exc))
            return

        server_id = int(result.get("id", 0)) or None
        self._db.mark_capture_sent(capture["id"], server_id, attempts)
        self._mark_online(connection_state)
        logger.info(
            "Captura %d enviada (id no servidor: %s%s)",
            capture["id"],
            server_id,
            ", duplicada" if result.get("duplicate") else "",
        )

    def _mark_online(self, connection_state: ConnectionState) -> None:
        self._consecutive_server_failures = 0
        connection_state.set_server_status(SERVER_ONLINE)

    def _register_auth_error(self, connection: Connection, exc: ApiAuthError) -> None:
        logger.warning("Token da conexão %d recusado: %s", connection.id, exc)
        self._last_auth_error_at[connection.id] = self._clock()
        self._state.connection(connection.id).set_server_status(SERVER_AUTH_ERROR, str(exc))

    def _register_server_failure(
        self, connections: list[Connection], exc: ApiUnavailableError
    ) -> None:
        self._consecutive_server_failures += 1
        for connection in connections:
            self._state.connection(connection.id).set_server_status(SERVER_OFFLINE, str(exc))
        backoff_index = min(
            self._consecutive_server_failures - 1,
            len(UPLOAD_BACKOFF_STEPS_SECONDS) - 1,
        )
        self._wait(UPLOAD_BACKOFF_STEPS_SECONDS[backoff_index])
