from dataclasses import asdict, dataclass, replace

from serial.tools import list_ports

from agent.constants import (
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_PARITY,
    DEFAULT_RTSCTS,
    DEFAULT_STOPBITS,
    MAX_IDLE_TIMEOUT_SECONDS,
    MIN_IDLE_TIMEOUT_SECONDS,
)

VALID_BAUDRATES = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)
VALID_BYTESIZES = (5, 6, 7, 8)
VALID_PARITIES = ("N", "E", "O", "M", "S")
VALID_STOPBITS = (1, 1.5, 2)

_TRUE_VALUES = ("1", "true", "sim", "yes", "on")
_PORT_FIELD_UNKNOWN = "n/a"


@dataclass(frozen=True)
class SerialSettings:
    port: str | None
    baudrate: int = DEFAULT_BAUDRATE
    bytesize: int = DEFAULT_BYTESIZE
    parity: str = DEFAULT_PARITY
    stopbits: float = DEFAULT_STOPBITS
    rtscts: bool = DEFAULT_RTSCTS
    idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS

    @classmethod
    def defaults(cls) -> "SerialSettings":
        return cls(port=None)

    @classmethod
    def from_row(cls, row: dict) -> "SerialSettings":
        return cls(
            port=row["port"] or None,
            baudrate=int(row["baudrate"]),
            bytesize=int(row["bytesize"]),
            parity=str(row["parity"]),
            stopbits=normalize_stopbits(float(row["stopbits"])),
            rtscts=bool(row["rtscts"]),
            idle_timeout_seconds=float(row["idle_timeout_seconds"]),
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.port)

    def with_baudrate(self, baudrate: int) -> "SerialSettings":
        return replace(self, baudrate=baudrate)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_api_payload(self) -> dict:
        return {
            "port": self.port,
            "baudrate": self.baudrate,
            "bytesize": self.bytesize,
            "parity": self.parity,
            "stopbits": self.stopbits,
            "rtscts": self.rtscts,
        }


def parse_serial_settings(raw: dict) -> SerialSettings:
    port = str(raw.get("port") or "").strip() or None

    baudrate = _parse_choice(raw.get("baudrate"), VALID_BAUDRATES, int, "Baudrate")
    bytesize = _parse_choice(raw.get("bytesize"), VALID_BYTESIZES, int, "Bits de dados")
    stopbits = _parse_choice(raw.get("stopbits"), VALID_STOPBITS, float, "Stop bits")

    parity = str(raw.get("parity") or "").strip().upper()
    if parity not in VALID_PARITIES:
        raise ValueError(f"Paridade inválida. Opções: {', '.join(VALID_PARITIES)}")

    idle_timeout = _parse_float(raw.get("idle_timeout_seconds"), "Tempo de silêncio")
    if not MIN_IDLE_TIMEOUT_SECONDS <= idle_timeout <= MAX_IDLE_TIMEOUT_SECONDS:
        raise ValueError(
            "Tempo de silêncio deve ficar entre "
            f"{MIN_IDLE_TIMEOUT_SECONDS} e {MAX_IDLE_TIMEOUT_SECONDS} segundos"
        )

    return SerialSettings(
        port=port,
        baudrate=baudrate,
        bytesize=bytesize,
        parity=parity,
        stopbits=normalize_stopbits(stopbits),
        rtscts=parse_bool(raw.get("rtscts")),
        idle_timeout_seconds=idle_timeout,
    )


def list_serial_ports() -> list[dict]:
    ports = []
    for info in sorted(list_ports.comports(), key=lambda item: item.device):
        ports.append(
            {
                "device": info.device,
                "description": _port_field(info.description),
                "hwid": _port_field(info.hwid),
            }
        )
    identified = [port for port in ports if port["hwid"]]
    return identified or ports


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE_VALUES


def normalize_stopbits(value: float) -> float:
    return int(value) if float(value).is_integer() else float(value)


def _port_field(value: str | None) -> str:
    return "" if not value or value == _PORT_FIELD_UNKNOWN else value


def _parse_choice(value: object, valid: tuple, cast, label: str):
    try:
        parsed = cast(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} inválido") from exc
    if parsed not in valid:
        options = ", ".join(str(normalize_stopbits(option)) for option in valid)
        raise ValueError(f"{label} inválido. Opções: {options}")
    return parsed


def _parse_float(value: object, label: str) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} inválido") from exc
