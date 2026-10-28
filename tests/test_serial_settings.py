import pytest

from agent import serial_settings
from agent.serial_settings import SerialSettings, parse_serial_settings


def test_defaults_match_the_lab_machine() -> None:
    settings = SerialSettings.defaults()

    assert settings.port is None
    assert not settings.is_configured
    assert (settings.baudrate, settings.bytesize, settings.parity) == (57600, 8, "N")
    assert settings.stopbits == 1
    assert settings.rtscts is True
    assert settings.idle_timeout_seconds == 3.0


def test_from_row_and_api_payload() -> None:
    row = {
        "port": "/dev/ttyUSB0",
        "baudrate": 9600,
        "bytesize": 7,
        "parity": "E",
        "stopbits": 2.0,
        "rtscts": 0,
        "idle_timeout_seconds": 1.5,
    }

    settings = SerialSettings.from_row(row)

    assert settings == SerialSettings(
        port="/dev/ttyUSB0", baudrate=9600, bytesize=7, parity="E", stopbits=2,
        rtscts=False, idle_timeout_seconds=1.5,
    )
    assert settings.to_api_payload() == {
        "port": "/dev/ttyUSB0", "baudrate": 9600, "bytesize": 7, "parity": "E",
        "stopbits": 2, "rtscts": False,
    }


def test_parse_from_form_strings() -> None:
    settings = parse_serial_settings(
        {
            "port": " COM13 ",
            "baudrate": "57600",
            "bytesize": "8",
            "parity": "n",
            "stopbits": "1",
            "idle_timeout_seconds": "2,5",
            "rtscts": True,
        }
    )

    assert settings == SerialSettings(
        port="COM13", baudrate=57600, bytesize=8, parity="N", stopbits=1,
        rtscts=True, idle_timeout_seconds=2.5,
    )


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("baudrate", "1234", "Baudrate"),
        ("bytesize", "9", "Bits de dados"),
        ("parity", "X", "Paridade"),
        ("stopbits", "3", "Stop bits"),
        ("idle_timeout_seconds", "0", "Tempo de silêncio"),
        ("idle_timeout_seconds", "abc", "Tempo de silêncio"),
    ],
)
def test_parse_rejects_invalid_values(field: str, value: str, fragment: str) -> None:
    raw = {
        "port": "COM1", "baudrate": "9600", "bytesize": "8", "parity": "N",
        "stopbits": "1", "idle_timeout_seconds": "3", "rtscts": False,
    }
    raw[field] = value

    with pytest.raises(ValueError, match=fragment):
        parse_serial_settings(raw)


class FakePortInfo:
    def __init__(self, device: str, description: str, hwid: str) -> None:
        self.device = device
        self.description = description
        self.hwid = hwid


def test_list_ports_hides_ghost_ports_when_a_real_one_exists(monkeypatch) -> None:
    fake_ports = [
        FakePortInfo("/dev/ttyS0", "n/a", "n/a"),
        FakePortInfo("/dev/ttyUSB0", "USB-SERIAL CH340", "USB VID:PID=1A86:7523"),
    ]
    monkeypatch.setattr(serial_settings.list_ports, "comports", lambda: fake_ports)

    assert serial_settings.list_serial_ports() == [
        {
            "device": "/dev/ttyUSB0",
            "description": "USB-SERIAL CH340",
            "hwid": "USB VID:PID=1A86:7523",
        }
    ]


def test_list_ports_keeps_ghost_ports_when_nothing_else(monkeypatch) -> None:
    monkeypatch.setattr(
        serial_settings.list_ports, "comports", lambda: [FakePortInfo("/dev/ttyS0", "n/a", "n/a")]
    )

    assert serial_settings.list_serial_ports() == [
        {"device": "/dev/ttyS0", "description": "", "hwid": ""}
    ]
