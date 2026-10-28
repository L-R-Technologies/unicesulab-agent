"""Estado compartilhado entre os workers (serial e envio) e a interface.

Servidor e porta serial têm status independentes: dá para configurar a porta
e capturar localmente antes de parear com o servidor (e vice-versa).
"""

import threading

from agent.constants import LIVE_TEXT_MAX_CHARS

SERVER_STARTING = "starting"
SERVER_NOT_PAIRED = "not_paired"
SERVER_ONLINE = "online"
SERVER_OFFLINE = "offline"
SERVER_AUTH_ERROR = "auth_error"

SERIAL_STARTING = "starting"
SERIAL_NOT_CONFIGURED = "not_configured"
SERIAL_CONNECTING = "connecting"
SERIAL_LISTENING = "listening"
SERIAL_DETECTING = "detecting"
SERIAL_ERROR = "error"

DETECT_IDLE = "idle"
DETECT_RUNNING = "running"
DETECT_DONE = "done"


class AgentState:
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
        # Pareamento mudou (URL/token): o worker de envio recria o cliente
        self.pairing_changed = threading.Event()
        # Configuração serial mudou ou o operador pediu reconexão
        self.serial_config_changed = threading.Event()
        # Operador pediu detecção de baudrate (executada pelo worker serial)
        self.detect_requested = threading.Event()
        # Há captura nova ou reenvio pedido: acorda o worker de envio
        self.upload_requested = threading.Event()
        # Força um heartbeat imediato (botão "Tentar novamente")
        self.heartbeat_requested = threading.Event()

    # --- Servidor ---

    def set_server_status(self, status: str, error_message: str | None = None) -> None:
        with self._lock:
            self._server_status = status
            self._server_error_message = error_message

    def set_pairing_info(self, agent_name: str | None, machine: dict | None) -> None:
        with self._lock:
            self._agent_name = agent_name
            self._machine = machine

    # --- Porta serial ---

    def set_serial_status(self, status: str, error_message: str | None = None) -> None:
        with self._lock:
            self._serial_status = status
            self._serial_error_message = error_message

    def append_live_text(self, text: str) -> None:
        with self._lock:
            combined = self._live_text + text
            # Mantém só o fim do buffer para a tela não crescer sem limite
            self._live_text = combined[-LIVE_TEXT_MAX_CHARS:]

    def clear_live_text(self) -> None:
        with self._lock:
            self._live_text = ""

    # --- Detecção de baudrate ---

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
