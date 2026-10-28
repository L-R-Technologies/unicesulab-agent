import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import serial

from agent.constants import MAX_CAPTURE_BYTES, SERIAL_READ_TIMEOUT_SECONDS
from agent.serial_settings import SerialSettings

_EXAM_LINE_PATTERN = re.compile(r"^\s*EXAME\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_TEXT_ENCODING = "latin-1"


class SerialOpenError(Exception):
    pass


class SerialReadError(Exception):
    pass


class ByteSource(Protocol):
    def read_available(self) -> bytes: ...

    def close(self) -> None: ...


class PySerialSource:
    def __init__(self, connection: serial.Serial) -> None:
        self._connection = connection

    def read_available(self) -> bytes:
        try:
            pending = self._connection.in_waiting
            return self._connection.read(pending or 1)
        except (serial.SerialException, OSError) as exc:
            raise SerialReadError(f"Falha ao ler a porta serial: {exc}") from exc

    def close(self) -> None:
        try:
            self._connection.close()
        except (serial.SerialException, OSError):
            return


def open_serial_port(settings: SerialSettings) -> PySerialSource:
    if not settings.port:
        raise SerialOpenError("Nenhuma porta serial configurada")
    try:
        connection = serial.Serial(
            port=settings.port,
            baudrate=settings.baudrate,
            bytesize=settings.bytesize,
            parity=settings.parity,
            stopbits=settings.stopbits,
            timeout=SERIAL_READ_TIMEOUT_SECONDS,
            rtscts=settings.rtscts,
        )
    except (serial.SerialException, OSError, ValueError) as exc:
        raise SerialOpenError(f"Não foi possível abrir {settings.port}: {exc}") from exc
    return PySerialSource(connection)


class CaptureFramer:
    def __init__(
        self,
        idle_timeout_seconds: float,
        max_bytes: int = MAX_CAPTURE_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._idle_timeout = idle_timeout_seconds
        self._max_bytes = max_bytes
        self._clock = clock
        self._buffer = bytearray()
        self._last_data_at: float | None = None

    @property
    def has_pending_data(self) -> bool:
        return bool(self._buffer)

    def feed(self, data: bytes) -> list[bytes]:
        if not data:
            return []
        self._buffer.extend(data)
        self._last_data_at = self._clock()

        completed = []
        while len(self._buffer) >= self._max_bytes:
            completed.append(bytes(self._buffer[: self._max_bytes]))
            del self._buffer[: self._max_bytes]
        return completed

    def poll(self) -> bytes | None:
        if not self._buffer or self._last_data_at is None:
            return None
        if self._clock() - self._last_data_at < self._idle_timeout:
            return None
        return self.flush()

    def flush(self) -> bytes | None:
        if not self._buffer:
            return None
        data = bytes(self._buffer)
        self._buffer.clear()
        self._last_data_at = None
        return data


@dataclass(frozen=True)
class Capture:
    raw_bytes: bytes
    text: str
    raw_hash: str
    captured_at: datetime

    @property
    def raw_bytes_hex(self) -> str:
        return self.raw_bytes.hex()

    @property
    def captured_at_iso(self) -> str:
        return self.captured_at.isoformat(timespec="seconds")


def normalize_capture_text(raw: bytes) -> str:
    text = raw.decode(_TEXT_ENCODING, errors="replace")
    text = text.replace("\r\n", "\n").replace("\n\r", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip("\n")


def decode_live_text(chunk: bytes) -> str:
    return chunk.decode(_TEXT_ENCODING, errors="replace").replace("\r", "")


def hash_capture_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_capture(raw_bytes: bytes, captured_at: datetime) -> Capture | None:
    text = normalize_capture_text(raw_bytes)
    if not text.strip():
        return None
    return Capture(
        raw_bytes=raw_bytes,
        text=text,
        raw_hash=hash_capture_text(text),
        captured_at=captured_at,
    )


def extract_capture_preview(text: str) -> str:
    match = _EXAM_LINE_PATTERN.search(text)
    if match:
        return match.group(1)
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""
