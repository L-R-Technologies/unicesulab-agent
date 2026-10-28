import pytest

from agent.connection import Connection, parse_connection_payload
from agent.serial_settings import SerialSettings

VALID_PAYLOAD = {
    "name": " Coagulômetro ",
    "port": "COM13",
    "baudrate": "57600",
    "bytesize": "8",
    "parity": "N",
    "stopbits": "1",
    "idle_timeout_seconds": "3",
    "rtscts": True,
    "agent_token": " abc ",
}


def test_parse_new_connection() -> None:
    connection = parse_connection_payload(VALID_PAYLOAD)

    assert connection.id is None
    assert connection.name == "Coagulômetro"
    assert connection.agent_token == "abc"
    assert connection.enabled is True
    assert connection.serial.port == "COM13"
    assert connection.to_db_fields()["rtscts"] == 1
    assert connection.to_public_dict() == {
        "id": None,
        "name": "Coagulômetro",
        "enabled": True,
        "has_token": True,
        "serial": connection.serial.to_dict(),
    }


def test_blank_token_keeps_existing_token_on_edit() -> None:
    existing = Connection(
        id=7, name="Antiga", serial=SerialSettings(port="COM1"), agent_token="segredo"
    )

    connection = parse_connection_payload({**VALID_PAYLOAD, "agent_token": ""}, existing)

    assert connection.id == 7
    assert connection.agent_token == "segredo"
    assert connection.name == "Coagulômetro"


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("name", "   ", "nome"),
        ("name", "x" * 61, "no máximo"),
        ("port", "", "porta"),
        ("agent_token", "", "token"),
        ("baudrate", "7", "Baudrate"),
    ],
)
def test_parse_rejects_invalid_payload(field: str, value: str, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        parse_connection_payload({**VALID_PAYLOAD, field: value})


def test_from_row_roundtrip() -> None:
    connection = parse_connection_payload({**VALID_PAYLOAD, "enabled": "0"})
    row = {"id": 3, **connection.to_db_fields()}

    restored = Connection.from_row(row)

    assert restored.id == 3
    assert restored.enabled is False
    assert restored.serial == connection.serial
    assert restored.agent_token == "abc"
