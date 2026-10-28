import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agent.constants import (
    BAUDRATE_CANDIDATES,
    DETECT_LISTEN_SECONDS,
    DETECT_PRINTABLE_THRESHOLD,
)
from agent.serial_reader import ByteSource, SerialOpenError, SerialReadError
from agent.serial_settings import SerialSettings

logger = logging.getLogger(__name__)

_PRINTABLE_MIN = 0x20
_PRINTABLE_MAX = 0x7E
_PRINTABLE_CONTROL = frozenset(b"\n\r\t")

SourceFactory = Callable[[SerialSettings], ByteSource]


@dataclass(frozen=True)
class BaudScore:
    baudrate: int
    bytes_received: int
    printable_ratio: float
    error: str | None = None


@dataclass(frozen=True)
class DetectionResult:
    best_baudrate: int | None
    scores: list[BaudScore] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "best_baudrate": self.best_baudrate,
            "message": self.message,
            "scores": [
                {
                    "baudrate": score.baudrate,
                    "bytes_received": score.bytes_received,
                    "printable_ratio": round(score.printable_ratio, 3),
                    "error": score.error,
                }
                for score in self.scores
            ],
        }


def printable_ratio(data: bytes) -> float:
    if not data:
        return 0.0
    printable = sum(
        1 for byte in data if _PRINTABLE_MIN <= byte <= _PRINTABLE_MAX or byte in _PRINTABLE_CONTROL
    )
    return printable / len(data)


def detect_baudrate(
    settings: SerialSettings,
    source_factory: SourceFactory,
    candidates: tuple[int, ...] = BAUDRATE_CANDIDATES,
    listen_seconds: float = DETECT_LISTEN_SECONDS,
    threshold: float = DETECT_PRINTABLE_THRESHOLD,
    clock: Callable[[], float] = time.monotonic,
    stop_event: threading.Event | None = None,
) -> DetectionResult:
    scores = []
    for baudrate in candidates:
        if stop_event is not None and stop_event.is_set():
            break
        scores.append(
            _score_baudrate(
                settings.with_baudrate(baudrate), source_factory, listen_seconds, clock
            )
        )

    return _choose_best(scores, threshold)


def _score_baudrate(
    settings: SerialSettings,
    source_factory: SourceFactory,
    listen_seconds: float,
    clock: Callable[[], float],
) -> BaudScore:
    logger.info("Detecção: escutando %s a %d baud", settings.port, settings.baudrate)
    try:
        source = source_factory(settings)
    except SerialOpenError as exc:
        return BaudScore(settings.baudrate, 0, 0.0, error=str(exc))

    received = bytearray()
    deadline = clock() + listen_seconds
    try:
        while clock() < deadline:
            received.extend(source.read_available())
    except SerialReadError as exc:
        return BaudScore(settings.baudrate, len(received), printable_ratio(received), str(exc))
    finally:
        source.close()

    return BaudScore(settings.baudrate, len(received), printable_ratio(bytes(received)))


def _choose_best(scores: list[BaudScore], threshold: float) -> DetectionResult:
    with_data = [score for score in scores if score.bytes_received > 0]
    if not with_data:
        return DetectionResult(
            None,
            scores,
            "Nenhum dado recebido. Dispare um resultado na máquina durante a detecção.",
        )

    best = max(with_data, key=lambda score: (score.printable_ratio, score.bytes_received))
    if best.printable_ratio < threshold:
        return DetectionResult(
            None,
            scores,
            f"Dados recebidos, mas ilegíveis em todas as velocidades (melhor: {best.baudrate} "
            f"baud com {best.printable_ratio:.0%} legível). Confira bits, paridade e RTS/CTS.",
        )

    return DetectionResult(
        best.baudrate,
        scores,
        f"Baudrate identificado: {best.baudrate} ({best.printable_ratio:.0%} legível, "
        f"{best.bytes_received} bytes).",
    )
