"""Parâmetros da porta serial: validação, persistência e listagem de portas."""

from dataclasses import asdict, dataclass, replace

from serial.tools import list_ports

from agent.constants import (
    CONFIG_SERIAL_BAUDRATE,
    CONFIG_SERIAL_BYTESIZE,
    CONFIG_SERIAL_IDLE_TIMEOUT,
    CONFIG_SERIAL_PARITY,
    CONFIG_SERIAL_PORT,
    CONFIG_SERIAL_RTSCTS,
    CONFIG_SERIAL_STOPBITS,
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_PARITY,
    DEFAULT_RTSCTS,
    DEFAULT_STOPBITS,
    MAX_IDLE_TIMEOUT_SECONDS,
    MIN_IDLE_TIMEOUT_SECONDS,
)
from agent.database import LocalDatabase

VALID_BAUDRATES = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)
VALID_BYTESIZES = (5, 6, 7, 8)
# Mesmas letras do pyserial: None, Even, Odd, Mark, Space
VALID_PARITIES = ("N", "E", "O", "M", "S")
VALID_STOPBITS = (1, 1.5, 2)

_TRUE_VALUES = ("1", "true", "sim", "yes", "on")
# Valor que o pyserial usa quando não sabe descrever a porta
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

    @property
    def is_configured(self) -> bool:
        return bool(self.port)

    def with_baudrate(self, baudrate: int) -> "SerialSettings":
        return replace(self, baudrate=baudrate)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_api_payload(self) -> dict:
        """Formato enviado ao servidor junto com cada captura."""
        return {
            "port": self.port,
            "baudrate": self.baudrate,
            "bytesize": self.bytesize,
            "parity": self.parity,
            "stopbits": self.stopbits,
            "rtscts": self.rtscts,
        }

    @classmethod
    def load(cls, local_db: LocalDatabase) -> "SerialSettings":
        defaults = cls(port=None)
        return cls(
            port=local_db.get_config(CONFIG_SERIAL_PORT) or None,
            baudrate=_read_int(local_db, CONFIG_SERIAL_BAUDRATE, defaults.baudrate),
            bytesize=_read_int(local_db, CONFIG_SERIAL_BYTESIZE, defaults.bytesize),
            parity=local_db.get_config(CONFIG_SERIAL_PARITY) or defaults.parity,
            stopbits=_read_float(local_db, CONFIG_SERIAL_STOPBITS, defaults.stopbits),
            rtscts=_read_bool(local_db, CONFIG_SERIAL_RTSCTS, defaults.rtscts),
            idle_timeout_seconds=_read_float(
                local_db, CONFIG_SERIAL_IDLE_TIMEOUT, defaults.idle_timeout_seconds
            ),
        )

    def save(self, local_db: LocalDatabase) -> None:
        if self.port:
            local_db.set_config(CONFIG_SERIAL_PORT, self.port)
        else:
            local_db.delete_config(CONFIG_SERIAL_PORT)
        local_db.set_config(CONFIG_SERIAL_BAUDRATE, str(self.baudrate))
        local_db.set_config(CONFIG_SERIAL_BYTESIZE, str(self.bytesize))
        local_db.set_config(CONFIG_SERIAL_PARITY, self.parity)
        local_db.set_config(CONFIG_SERIAL_STOPBITS, str(self.stopbits))
        local_db.set_config(CONFIG_SERIAL_RTSCTS, "1" if self.rtscts else "0")
        local_db.set_config(CONFIG_SERIAL_IDLE_TIMEOUT, str(self.idle_timeout_seconds))


def parse_serial_settings(raw: dict) -> SerialSettings:
    """Valida os valores vindos da interface. Lança ValueError com mensagem em pt-BR."""
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
        stopbits=_normalize_stopbits(stopbits),
        rtscts=_parse_bool(raw.get("rtscts")),
        idle_timeout_seconds=idle_timeout,
    )


def list_serial_ports() -> list[dict]:
    """Portas visíveis no sistema (COMx no Windows, /dev/ttyUSBx no Linux)."""
    ports = []
    for info in sorted(list_ports.comports(), key=lambda item: item.device):
        ports.append(
            {
                "device": info.device,
                "description": _port_field(info.description),
                "hwid": _port_field(info.hwid),
            }
        )
    # No Linux o kernel lista dezenas de /dev/ttyS* sem hardware atrás; esconde
    # essas quando existe ao menos uma porta identificada (USB-serial etc.)
    identified = [port for port in ports if port["hwid"]]
    return identified or ports


def _port_field(value: str | None) -> str:
    return "" if not value or value == _PORT_FIELD_UNKNOWN else value


def _parse_choice(value: object, valid: tuple, cast, label: str):
    try:
        parsed = cast(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} inválido") from exc
    if parsed not in valid:
        options = ", ".join(str(_normalize_stopbits(option)) for option in valid)
        raise ValueError(f"{label} inválido. Opções: {options}")
    return parsed


def _parse_float(value: object, label: str) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} inválido") from exc


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE_VALUES


def _normalize_stopbits(value: float) -> float:
    # Guarda 1 e 2 como inteiros para a UI e o servidor não verem "1.0"
    return int(value) if float(value).is_integer() else float(value)


def _read_int(local_db: LocalDatabase, key: str, default: int) -> int:
    value = local_db.get_config(key)
    return int(value) if value is not None else default


def _read_float(local_db: LocalDatabase, key: str, default: float) -> float:
    value = local_db.get_config(key)
    return _normalize_stopbits(float(value)) if value is not None else default


def _read_bool(local_db: LocalDatabase, key: str, default: bool) -> bool:
    value = local_db.get_config(key)
    return _parse_bool(value) if value is not None else default
