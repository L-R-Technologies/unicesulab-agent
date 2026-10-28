"""API exposta ao JavaScript da janela pywebview."""

import logging
from collections.abc import Callable

from agent import __version__
from agent.api_client import ApiAuthError, ApiClient, ApiError, ApiUnavailableError
from agent.constants import (
    CONFIG_AGENT_TOKEN,
    CONFIG_PAIRING_SKIPPED,
    CONFIG_SERVER_URL,
    RECENT_CAPTURES_LIMIT,
)
from agent.database import LocalDatabase
from agent.serial_reader import extract_capture_preview
from agent.serial_settings import (
    VALID_BAUDRATES,
    VALID_BYTESIZES,
    VALID_PARITIES,
    VALID_STOPBITS,
    SerialSettings,
    list_serial_ports,
    parse_serial_settings,
)
from agent.state import AgentState

logger = logging.getLogger(__name__)


class JsBridge:
    """Métodos chamados pelo front da janela (pywebview js_api)."""

    def __init__(
        self,
        local_db: LocalDatabase,
        state: AgentState,
        on_quit: Callable[[], None] | None = None,
    ) -> None:
        self._db = local_db
        self._state = state
        self._on_quit = on_quit

    def get_state(self) -> dict:
        server_url = self._db.get_config(CONFIG_SERVER_URL)
        token = self._db.get_config(CONFIG_AGENT_TOKEN)

        snapshot = self._state.snapshot()
        snapshot.update(
            {
                "agent_version": __version__,
                "server_url": server_url,
                "is_paired": bool(server_url and token),
                "pairing_skipped": self._db.get_config(CONFIG_PAIRING_SKIPPED) is not None,
                "serial_settings": SerialSettings.load(self._db).to_dict(),
                "serial_options": {
                    "baudrates": list(VALID_BAUDRATES),
                    "bytesizes": list(VALID_BYTESIZES),
                    "parities": list(VALID_PARITIES),
                    "stopbits": list(VALID_STOPBITS),
                },
                "recent_captures": self._recent_captures(),
            }
        )
        return snapshot

    def _recent_captures(self) -> list[dict]:
        captures = self._db.list_recent_captures(RECENT_CAPTURES_LIMIT)
        for capture in captures:
            capture["preview"] = extract_capture_preview(capture.pop("raw_text"))
        return captures

    # --- Pareamento ---

    def skip_pairing(self) -> dict:
        """Permite configurar a porta serial antes de parear com o servidor."""
        self._db.set_config(CONFIG_PAIRING_SKIPPED, "1")
        return {"ok": True}

    def save_pairing(self, server_url: str, token: str) -> dict:
        server_url = server_url.strip().rstrip("/")
        token = token.strip()

        if not server_url.startswith(("http://", "https://")):
            return {"ok": False, "error": "Informe a URL completa (http:// ou https://)"}
        if not token:
            return {"ok": False, "error": "Informe o token do agente"}

        try:
            info = ApiClient(server_url, token).ping()
        except ApiAuthError:
            return {
                "ok": False,
                "error": "Token recusado pelo servidor. Confira o token do agente.",
            }
        except ApiUnavailableError:
            return {"ok": False, "error": "Servidor inacessível. Confira a URL e a conexão."}
        except ApiError:
            logger.exception("Resposta inesperada ao validar pareamento")
            return {"ok": False, "error": "Resposta inesperada do servidor. Veja os logs."}

        self._db.set_config(CONFIG_SERVER_URL, server_url)
        self._db.set_config(CONFIG_AGENT_TOKEN, token)
        self._db.delete_config(CONFIG_PAIRING_SKIPPED)
        agent_info = info.get("agent") or {}
        self._state.set_pairing_info(agent_info.get("name"), info.get("machine"))
        self._state.pairing_changed.set()
        self._state.heartbeat_requested.set()
        self._state.upload_requested.set()
        return {"ok": True}

    def retry_connection(self) -> dict:
        self._state.heartbeat_requested.set()
        self._state.upload_requested.set()
        return {"ok": True}

    # --- Porta serial ---

    def list_ports(self) -> dict:
        try:
            return {"ok": True, "ports": list_serial_ports()}
        except Exception:
            logger.exception("Erro ao listar portas seriais")
            return {"ok": False, "error": "Erro ao listar portas. Veja os logs."}

    def save_serial_settings(self, raw_settings: dict) -> dict:
        try:
            settings = parse_serial_settings(raw_settings)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        settings.save(self._db)
        self._state.clear_detect_result()
        self._state.serial_config_changed.set()
        return {"ok": True, "serial_settings": settings.to_dict()}

    def reconnect_serial(self) -> dict:
        self._state.serial_config_changed.set()
        return {"ok": True}

    def detect_baudrate(self) -> dict:
        if not SerialSettings.load(self._db).is_configured:
            return {"ok": False, "error": "Escolha e salve a porta serial antes de detectar."}
        self._state.set_detect_running()
        self._state.detect_requested.set()
        return {"ok": True}

    def dismiss_detect_result(self) -> dict:
        self._state.clear_detect_result()
        return {"ok": True}

    def clear_console(self) -> dict:
        self._state.clear_live_text()
        return {"ok": True}

    # --- Capturas ---

    def retry_failed_captures(self) -> dict:
        count = self._db.reset_failed_captures()
        self._state.upload_requested.set()
        return {"ok": True, "count": count}

    def quit_app(self) -> dict:
        if self._on_quit:
            self._on_quit()
        return {"ok": True}
