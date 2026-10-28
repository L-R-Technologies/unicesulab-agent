from dataclasses import dataclass, replace

from agent.constants import MAX_CONNECTION_NAME_LENGTH
from agent.serial_settings import SerialSettings, parse_bool, parse_serial_settings


@dataclass(frozen=True)
class Connection:
    id: int | None
    name: str
    serial: SerialSettings
    agent_token: str
    enabled: bool = True

    @classmethod
    def from_row(cls, row: dict) -> "Connection":
        return cls(
            id=int(row["id"]),
            name=str(row["name"]),
            serial=SerialSettings.from_row(row),
            agent_token=str(row["agent_token"]),
            enabled=bool(row["enabled"]),
        )

    @property
    def has_token(self) -> bool:
        return bool(self.agent_token)

    def with_serial(self, serial: SerialSettings) -> "Connection":
        return replace(self, serial=serial)

    def to_db_fields(self) -> dict:
        return {
            "name": self.name,
            "port": self.serial.port or "",
            "baudrate": self.serial.baudrate,
            "bytesize": self.serial.bytesize,
            "parity": self.serial.parity,
            "stopbits": self.serial.stopbits,
            "rtscts": int(self.serial.rtscts),
            "idle_timeout_seconds": self.serial.idle_timeout_seconds,
            "agent_token": self.agent_token,
            "enabled": int(self.enabled),
        }

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "has_token": self.has_token,
            "serial": self.serial.to_dict(),
        }


def parse_connection_payload(raw: dict, existing: Connection | None = None) -> Connection:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("Informe um nome para a máquina")
    if len(name) > MAX_CONNECTION_NAME_LENGTH:
        raise ValueError(f"O nome deve ter no máximo {MAX_CONNECTION_NAME_LENGTH} caracteres")

    serial = parse_serial_settings(raw)
    if not serial.is_configured:
        raise ValueError("Escolha a porta serial da máquina")

    agent_token = str(raw.get("agent_token") or "").strip()
    if not agent_token:
        if existing is None:
            raise ValueError("Informe o token do agente gerado na tela da máquina")
        agent_token = existing.agent_token

    enabled = parse_bool(raw["enabled"]) if "enabled" in raw else True

    return Connection(
        id=existing.id if existing else None,
        name=name,
        serial=serial,
        agent_token=agent_token,
        enabled=enabled,
    )
