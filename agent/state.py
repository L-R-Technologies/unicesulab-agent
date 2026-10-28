import threading

from agent.constants import LIVE_TEXT_MAX_CHARS

SERVER_STARTING = "starting"
SERVER_NOT_CONFIGURED = "not_configured"
SERVER_ONLINE = "online"
SERVER_OFFLINE = "offline"
SERVER_AUTH_ERROR = "auth_error"

SERIAL_STARTING = "starting"
SERIAL_DISABLED = "disabled"
SERIAL_CONNECTING = "connecting"
SERIAL_LISTENING = "listening"
SERIAL_DETECTING = "detecting"
SERIAL_ERROR = "error"

DETECT_IDLE = "idle"
DETECT_RUNNING = "running"
DETECT_DONE = "done"


class ConnectionState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._server_status = SERVER_STARTING
        self._server_error_message: str | None = None
        self._machine: dict | None = None
        self._agent_name: str | None = None
        self._serial_status = SERIAL_STARTING
        self._serial_error_message: str | None = None
        self._live_text = ""
        self._detect_status = DETECT_IDLE
        self._detect_result: dict | None = None
        self.config_changed = threading.Event()
        self.detect_requested = threading.Event()
        self.heartbeat_requested = threading.Event()

    def set_server_status(self, status: str, error_message: str | None = None) -> None:
        with self._lock:
            self._server_status = status
            self._server_error_message = error_message

    def server_status(self) -> str:
        with self._lock:
            return self._server_status

    def set_pairing_info(self, agent_name: str | None, machine: dict | None) -> None:
        with self._lock:
            self._agent_name = agent_name
            self._machine = machine

    def set_serial_status(self, status: str, error_message: str | None = None) -> None:
        with self._lock:
            self._serial_status = status
            self._serial_error_message = error_message

    def append_live_text(self, text: str) -> None:
        with self._lock:
            combined = self._live_text + text
            self._live_text = combined[-LIVE_TEXT_MAX_CHARS:]

    def clear_live_text(self) -> None:
        with self._lock:
            self._live_text = ""

    def set_detect_running(self) -> None:
        with self._lock:
            self._detect_status = DETECT_RUNNING
            self._detect_result = None

    def set_detect_result(self, result: dict) -> None:
        with self._lock:
            self._detect_status = DETECT_DONE
            self._detect_result = result

    def clear_detect_result(self) -> None:
        with self._lock:
            self._detect_status = DETECT_IDLE
            self._detect_result = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "server_status": self._server_status,
                "server_error_message": self._server_error_message,
                "agent_name": self._agent_name,
                "machine": self._machine,
                "serial_status": self._serial_status,
                "serial_error_message": self._serial_error_message,
                "live_text": self._live_text,
                "detect_status": self._detect_status,
                "detect_result": self._detect_result,
            }


class AgentState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connections: dict[int, ConnectionState] = {}
        self.connections_changed = threading.Event()
        self.server_url_changed = threading.Event()
        self.upload_requested = threading.Event()

    def connection(self, connection_id: int) -> ConnectionState:
        with self._lock:
            state = self._connections.get(connection_id)
            if state is None:
                state = ConnectionState()
                self._connections[connection_id] = state
            return state

    def remove_connection(self, connection_id: int) -> None:
        with self._lock:
            self._connections.pop(connection_id, None)

    def connection_ids(self) -> list[int]:
        with self._lock:
            return list(self._connections)

    def request_heartbeats(self) -> None:
        for state in self._all_connections():
            state.heartbeat_requested.set()
        self.upload_requested.set()

    def snapshot(self) -> dict[int, dict]:
        return {
            connection_id: state.snapshot()
            for connection_id, state in self._connection_items()
        }

    def _all_connections(self) -> list[ConnectionState]:
        with self._lock:
            return list(self._connections.values())

    def _connection_items(self) -> list[tuple[int, ConnectionState]]:
        with self._lock:
            return list(self._connections.items())
