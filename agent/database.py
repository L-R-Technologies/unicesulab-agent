"""Persistência local em SQLite: configuração e fila de capturas."""

import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agent.constants import APP_NAME, DB_FILENAME

# Status locais das capturas. "captured" aguarda envio; "failed" é rejeição
# definitiva do servidor (ex.: validação) e só volta à fila por ação do operador.
CAPTURE_STATUS_CAPTURED = "captured"
CAPTURE_STATUS_SENT = "sent"
CAPTURE_STATUS_FAILED = "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_text TEXT NOT NULL,
    raw_bytes_hex TEXT NOT NULL,
    raw_hash TEXT NOT NULL UNIQUE,
    captured_at TEXT NOT NULL,
    status TEXT NOT NULL,
    server_capture_id INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def default_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", str(Path.home()))
        return Path(base) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


class LocalDatabase:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or default_data_dir() / DB_FILENAME
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # Conexão por operação: com WAL, captura e envio acessam o banco em
        # paralelo sem lock entre threads
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    # --- Configuração (key/value) ---

    def get_config(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_config(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO config (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def delete_config(self, key: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM config WHERE key = ?", (key,))

    # --- Fila de capturas ---

    def insert_capture(
        self, raw_text: str, raw_bytes_hex: str, raw_hash: str, captured_at: str
    ) -> int:
        """Guarda a captura e devolve o id. Texto idêntico já guardado reaproveita o registro."""
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO captures "
                "(raw_text, raw_bytes_hex, raw_hash, captured_at, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (raw_text, raw_bytes_hex, raw_hash, captured_at, CAPTURE_STATUS_CAPTURED),
            )
            row = connection.execute(
                "SELECT id FROM captures WHERE raw_hash = ?", (raw_hash,)
            ).fetchone()
        return int(row["id"])

    def get_capture(self, capture_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM captures WHERE id = ?", (capture_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_pending_captures(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM captures WHERE status = ? ORDER BY id",
                (CAPTURE_STATUS_CAPTURED,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_recent_captures(self, limit: int = 50) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, raw_text, captured_at, status, server_capture_id, attempts, "
                "error_message, updated_at FROM captures ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_capture_sent(self, capture_id: int, server_capture_id: int, attempts: int) -> None:
        self._update_capture_status(
            capture_id, CAPTURE_STATUS_SENT, attempts, server_capture_id, None
        )

    def mark_capture_failed(self, capture_id: int, attempts: int, error_message: str) -> None:
        self._update_capture_status(
            capture_id, CAPTURE_STATUS_FAILED, attempts, None, error_message
        )

    def mark_capture_attempt(self, capture_id: int, attempts: int, error_message: str) -> None:
        """Tentativa sem sucesso que será repetida (servidor fora do ar)."""
        self._update_capture_status(
            capture_id, CAPTURE_STATUS_CAPTURED, attempts, None, error_message
        )

    def reset_failed_captures(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE captures SET status = ?, error_message = NULL, "
                "updated_at = datetime('now', 'localtime') WHERE status = ?",
                (CAPTURE_STATUS_CAPTURED, CAPTURE_STATUS_FAILED),
            )
        return cursor.rowcount

    def _update_capture_status(
        self,
        capture_id: int,
        status: str,
        attempts: int,
        server_capture_id: int | None,
        error_message: str | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE captures SET status = ?, attempts = ?, server_capture_id = ?, "
                "error_message = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
                (status, attempts, server_capture_id, error_message, capture_id),
            )
