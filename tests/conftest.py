from pathlib import Path

import pytest

from agent.connection import Connection
from agent.database import LocalDatabase
from agent.serial_settings import SerialSettings

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_CAPTURED_AT = "2026-10-28T10:00:00-03:00"


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def coagulometer_sample() -> bytes:
    return (FIXTURES_DIR / "coagulometro_sample.bin").read_bytes()


@pytest.fixture
def local_db(tmp_path: Path) -> LocalDatabase:
    return LocalDatabase(tmp_path / "test.db")


def split_sample_blocks(sample: bytes) -> list[bytes]:
    header = b"UNICESUMAR"
    starts = []
    index = sample.find(header)
    while index != -1:
        starts.append(index)
        index = sample.find(header, index + len(header))
    ends = starts[1:] + [len(sample)]
    return [sample[start:end] for start, end in zip(starts, ends, strict=True)]


def add_connection(
    local_db: LocalDatabase,
    name: str = "Coagulômetro",
    port: str = "COM13",
    token: str = "token",
    enabled: bool = True,
    idle_timeout_seconds: float = 3.0,
) -> int:
    connection = Connection(
        id=None,
        name=name,
        serial=SerialSettings(port=port, idle_timeout_seconds=idle_timeout_seconds),
        agent_token=token,
        enabled=enabled,
    )
    return local_db.insert_connection(connection.to_db_fields())


def insert_capture(local_db: LocalDatabase, connection_id: int, text: str) -> int:
    return local_db.insert_capture(
        connection_id, text, text.encode().hex(), f"hash-{text}", SAMPLE_CAPTURED_AT
    )
