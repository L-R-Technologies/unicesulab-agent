import logging
from collections.abc import Callable

from agent import __version__
from agent.api_client import ApiAuthError, ApiClient, ApiError, ApiUnavailableError
from agent.connection import Connection, parse_connection_payload
from agent.constants import CONFIG_SERVER_URL, CONFIG_SETUP_SKIPPED, RECENT_CAPTURES_LIMIT
from agent.database import LocalDatabase
from agent.serial_reader import extract_capture_preview
from agent.serial_settings import (
    VALID_BAUDRATES,
    VALID_BYTESIZES,
    VALID_PARITIES,
    VALID_STOPBITS,
    SerialSettings,
    list_serial_ports,
    parse_bool,
)
from agent.state import AgentState
from agent.upload_worker import ClientFactory

logger = logging.getLogger(__name__)

_URL_SCHEMES = ("http://", "https://")


class JsBridge:
    def __init__(
        self,
        local_db: LocalDatabase,
        state: AgentState,
        on_quit: Callable[[], None] | None = None,
        client_factory: ClientFactory = ApiClient,
    ) -> None:
        self._db = local_db
        self._state = state
        self._on_quit = on_quit
        self._client_factory = client_factory

    def get_state(self) -> dict:
        server_url = self._db.get_config(CONFIG_SERVER_URL)
        return {
            "agent_version": __version__,
            "server_url": server_url,
            "is_server_configured": bool(server_url),
            "setup_skipped": self._db.get_config(CONFIG_SETUP_SKIPPED) is not None,
            "serial_defaults": SerialSettings.defaults().to_dict(),
            "serial_options": {
                "baudrates": list(VALID_BAUDRATES),
                "bytesizes": list(VALID_BYTESIZES),
                "parities": list(VALID_PARITIES),
                "stopbits": list(VALID_STOPBITS),
            },
            "connections": self._connections_view(),
            "recent_captures": self._recent_captures(),
        }

    def _connections_view(self) -> list[dict]:
        views = []
        for row in self._db.list_connections():
            connection = Connection.from_row(row)
            view = connection.to_public_dict()
            view.update(self._state.connection(connection.id).snapshot())
            views.append(view)
        return views

    def _recent_captures(self) -> list[dict]:
        captures = self._db.list_recent_captures(RECENT_CAPTURES_LIMIT)
        for capture in captures:
            capture["preview"] = extract_capture_preview(capture.pop("raw_text"))
        return captures

    def skip_setup(self) -> dict:
        self._db.set_config(CONFIG_SETUP_SKIPPED, "1")
        return {"ok": True}

    def save_server_url(self, server_url: str) -> dict:
        server_url = server_url.strip().rstrip("/")
        if not server_url.startswith(_URL_SCHEMES):
            return {"ok": False, "error": "Informe a URL completa, começando com http ou https"}

        self._db.set_config(CONFIG_SERVER_URL, server_url)
        self._db.delete_config(CONFIG_SETUP_SKIPPED)
        self._state.server_url_changed.set()
        self._state.request_heartbeats()
        return {"ok": True}

    def list_ports(self) -> dict:
        try:
            return {"ok": True, "ports": list_serial_ports()}
        except Exception:
            logger.exception("Erro ao listar portas seriais")
            return {"ok": False, "error": "Erro ao listar portas. Veja os logs."}

    def save_connection(self, payload: dict) -> dict:
        existing = self._find_connection(payload.get("id"))
        if payload.get("id") and existing is None:
            return {"ok": False, "error": "Máquina não encontrada. Atualize a tela."}

        try:
            connection = parse_connection_payload(payload, existing)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        pairing_info = None
        if not parse_bool(payload.get("skip_validation")):
            validation = self._validate_token(connection.agent_token)
            if not validation["ok"]:
                return validation
            pairing_info = validation["info"]

        connection_id = self._persist_connection(connection)
        self._notify_connection_saved(connection_id, pairing_info)
        return {"ok": True, "id": connection_id}

    def delete_connection(self, connection_id: int) -> dict:
        if self._find_connection(connection_id) is None:
            return {"ok": False, "error": "Máquina não encontrada. Atualize a tela."}
        self._db.delete_connection(int(connection_id))
        self._state.remove_connection(int(connection_id))
        self._state.connections_changed.set()
        return {"ok": True}

    def reconnect(self, connection_id: int) -> dict:
        self._state.connection(int(connection_id)).config_changed.set()
        return {"ok": True}

    def retry_connection(self, connection_id: int) -> dict:
        self._state.connection(int(connection_id)).heartbeat_requested.set()
        self._state.upload_requested.set()
        return {"ok": True}

    def detect_baudrate(self, connection_id: int) -> dict:
        connection = self._find_connection(connection_id)
        if connection is None or not connection.enabled:
            return {"ok": False, "error": "A máquina precisa estar salva e ativa para detectar."}
        connection_state = self._state.connection(connection.id)
        connection_state.set_detect_running()
        connection_state.detect_requested.set()
        return {"ok": True}

    def dismiss_detect_result(self, connection_id: int) -> dict:
        self._state.connection(int(connection_id)).clear_detect_result()
        return {"ok": True}

    def clear_console(self, connection_id: int) -> dict:
        self._state.connection(int(connection_id)).clear_live_text()
        return {"ok": True}

    def retry_failed_captures(self) -> dict:
        count = self._db.reset_failed_captures()
        self._state.upload_requested.set()
        return {"ok": True, "count": count}

    def quit_app(self) -> dict:
        if self._on_quit:
            self._on_quit()
        return {"ok": True}

    def _find_connection(self, connection_id: object) -> Connection | None:
        if connection_id in (None, ""):
            return None
        row = self._db.get_connection(int(connection_id))
        return Connection.from_row(row) if row else None

    def _validate_token(self, agent_token: str) -> dict:
        server_url = self._db.get_config(CONFIG_SERVER_URL)
        if not server_url:
            return {
                "ok": False,
                "error": "Informe o endereço do servidor antes de validar o token.",
            }

        try:
            info = self._client_factory(server_url, agent_token).ping()
        except ApiAuthError:
            return {
                "ok": False,
                "error": "Token recusado pelo servidor. Confira o token da máquina.",
            }
        except ApiUnavailableError:
            return {
                "ok": False,
                "error": "Servidor inacessível. Confira a URL ou marque \"salvar sem validar\".",
            }
        except ApiError:
            logger.exception("Resposta inesperada ao validar o token")
            return {"ok": False, "error": "Resposta inesperada do servidor. Veja os logs."}

        return {"ok": True, "info": info}

    def _persist_connection(self, connection: Connection) -> int:
        fields = connection.to_db_fields()
        if connection.id is None:
            return self._db.insert_connection(fields)
        self._db.update_connection(connection.id, fields)
        return connection.id

    def _notify_connection_saved(self, connection_id: int, pairing_info: dict | None) -> None:
        connection_state = self._state.connection(connection_id)
        if pairing_info is not None:
            agent_info = pairing_info.get("agent") or {}
            connection_state.set_pairing_info(agent_info.get("name"), pairing_info.get("machine"))
        connection_state.clear_detect_result()
        connection_state.config_changed.set()
        connection_state.heartbeat_requested.set()
        self._state.connections_changed.set()
        self._state.upload_requested.set()
