import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import webview

from agent.connection_manager import ConnectionManager
from agent.database import LocalDatabase, default_data_dir
from agent.state import AgentState
from agent.tray import create_tray_icon, stop_tray_icon
from agent.ui.bridge import JsBridge
from agent.upload_worker import UploadWorker

WINDOW_TITLE = "UNICESULAB — Agente de captura"
WINDOW_WIDTH = 1040
WINDOW_HEIGHT = 760
WINDOW_MIN_SIZE = (800, 600)
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3


def configure_logging() -> None:
    log_path = default_data_dir() / "agent.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
        ),
        logging.StreamHandler(),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def resolve_web_entry() -> str:
    base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return str(base_dir / "agent" / "ui" / "web" / "index.html")


class Application:
    def __init__(self) -> None:
        self._db = LocalDatabase()
        self._state = AgentState()
        self._connection_manager = ConnectionManager(self._db, self._state)
        self._upload_worker = UploadWorker(self._db, self._state)
        self._window: webview.Window | None = None
        self._tray_icon = None
        self._quitting = False

    def _stop_workers(self) -> None:
        self._connection_manager.stop()
        self._upload_worker.stop()

    def _quit(self) -> None:
        self._quitting = True
        self._stop_workers()
        if self._window is not None:
            self._window.destroy()

    def _show_window(self) -> None:
        if self._window is not None:
            self._window.show()

    def _on_window_closing(self) -> bool:
        if self._tray_icon is not None and not self._quitting:
            if self._window is not None:
                self._window.hide()
            return False
        self._stop_workers()
        return True

    def run(self) -> None:
        configure_logging()
        logging.getLogger(__name__).info("Iniciando agente de captura")

        self._connection_manager.start()
        self._upload_worker.start()
        self._tray_icon = create_tray_icon(on_open=self._show_window, on_quit=self._quit)

        bridge = JsBridge(self._db, self._state, on_quit=self._quit)
        self._window = webview.create_window(
            WINDOW_TITLE,
            resolve_web_entry(),
            js_api=bridge,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_size=WINDOW_MIN_SIZE,
        )
        self._window.events.closing += self._on_window_closing

        try:
            webview.start()
        finally:
            self._stop_workers()
            stop_tray_icon(self._tray_icon)


if __name__ == "__main__":
    Application().run()
