import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agent.constants import APP_NAME, DB_FILENAME

CAPTURE_STATUS_CAPTURED = "captured"
CAPTURE_STATUS_SENT = "sent"
CAPTURE_STATUS_FAILED = "failed"

CONNECTION_FIELDS = (
    "name",
    "port",
    "baudrate",
    "bytesize",
    "parity",
    "stopbits",
    "rtscts",
    "idle_timeout_seconds",
    "agent_token",
    "enabled",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    port TEXT NOT NULL,
    baudrate INTEGER NOT NULL,
    bytesize INTEGER NOT NULL,
    parity TEXT NOT NULL,
    stopbits REAL NOT NULL,
    rtscts INTEGER NOT NULL,
    idle_timeout_seconds REAL NOT NULL,
    agent_token TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id INTEGER NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
    raw_text TEXT NOT NULL,
    raw_bytes_hex TEXT NOT NULL,
    raw_hash TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    status TEXT NOT NULL,
    server_capture_id INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (connection_id, raw_hash)
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
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

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

    def insert_connection(self, fields: dict) -> int:
        columns = ", ".join(CONNECTION_FIELDS)
        placeholders = ", ".join("?" for _ in CONNECTION_FIELDS)
        with self._connect() as connection:
            cursor = connection.execute(
                f"INSERT INTO connections ({columns}) VALUES ({placeholders})",
                tuple(fields[field] for field in CONNECTION_FIELDS),
            )
        return int(cursor.lastrowid)

    def update_connection(self, connection_id: int, fields: dict) -> None:
        assignments = ", ".join(f"{field} = ?" for field in CONNECTION_FIELDS)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE connections SET {assignments}, "
                "updated_at = datetime('now', 'localtime') WHERE id = ?",
                (*(fields[field] for field in CONNECTION_FIELDS), connection_id),
            )

    def delete_connection(self, connection_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM connections WHERE id = ?", (connection_id,))

    def get_connection(self, connection_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM connections WHERE id = ?", (connection_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_connections(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM connections ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def insert_capture(
        self,
        connection_id: int,
        raw_text: str,
        raw_bytes_hex: str,
        raw_hash: str,
        captured_at: str,
    ) -> int:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO captures "
                "(connection_id, raw_text, raw_bytes_hex, raw_hash, captured_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    connection_id,
                    raw_text,
                    raw_bytes_hex,
                    raw_hash,
                    captured_at,
                    CAPTURE_STATUS_CAPTURED,
                ),
            )
            row = connection.execute(
                "SELECT id FROM captures WHERE connection_id = ? AND raw_hash = ?",
                (connection_id, raw_hash),
            ).fetchone()
        return int(row["id"])

    def get_capture(self, capture_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM captures WHERE id = ?", (capture_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_pending_captures(self, connection_id: int | None = None) -> list[dict]:
        query = "SELECT * FROM captures WHERE status = ?"
        parameters: tuple = (CAPTURE_STATUS_CAPTURED,)
        if connection_id is not None:
            query += " AND connection_id = ?"
            parameters = (*parameters, connection_id)
        with self._connect() as connection:
            rows = connection.execute(f"{query} ORDER BY id", parameters).fetchall()
        return [dict(row) for row in rows]

    def list_recent_captures(self, limit: int = 50) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT captures.id, captures.connection_id, connections.name AS connection_name, "
                "captures.raw_text, captures.captured_at, captures.status, "
                "captures.server_capture_id, captures.attempts, captures.error_message, "
                "captures.updated_at FROM captures "
                "LEFT JOIN connections ON connections.id = captures.connection_id "
                "ORDER BY captures.id DESC LIMIT ?",
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
