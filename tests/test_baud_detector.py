from agent.baud_detector import detect_baudrate, printable_ratio
from agent.serial_reader import SerialOpenError
from agent.serial_settings import SerialSettings
from tests.conftest import FakeClock

LISTEN_SECONDS = 4.0
READ_TIMEOUT = 1.0


class FakeSource:

    def __init__(self, clock: FakeClock, payload: bytes) -> None:
        self._clock = clock
        self._payload = payload
        self.closed = False

    def read_available(self) -> bytes:
        self._clock.advance(READ_TIMEOUT)
        payload, self._payload = self._payload, b""
        return payload

    def close(self) -> None:
        self.closed = True


def make_factory(
    clock: FakeClock, payload_by_baud: dict[int, bytes], failing: frozenset[int] = frozenset()
):
    sources: list[FakeSource] = []

    def factory(settings: SerialSettings) -> FakeSource:
        if settings.baudrate in failing:
            raise SerialOpenError(f"porta ocupada a {settings.baudrate}")
        source = FakeSource(clock, payload_by_baud.get(settings.baudrate, b""))
        sources.append(source)
        return source

    factory.sources = sources
    return factory


def test_printable_ratio() -> None:
    assert printable_ratio(b"") == 0.0
    assert printable_ratio(b"UNICESUMAR\n\r\t") == 1.0
    assert printable_ratio(b"ab\xff\x00") == 0.5


def test_detects_the_readable_baudrate(clock: FakeClock) -> None:
    garbage = bytes([0xFF, 0x80, 0x98, 0x66, 0x60]) * 20
    factory = make_factory(clock, {57600: b"UNICESUMAR\n\rEXAME: TP\n\r" * 5, 9600: garbage})

    result = detect_baudrate(
        SerialSettings(port="COM13"), factory, candidates=(115200, 57600, 9600), clock=clock
    )

    assert result.best_baudrate == 57600
    assert "57600" in result.message
    assert [score.baudrate for score in result.scores] == [115200, 57600, 9600]
    assert all(source.closed for source in factory.sources)


def test_no_bytes_in_any_baudrate(clock: FakeClock) -> None:
    factory = make_factory(clock, {})

    result = detect_baudrate(SerialSettings(port="COM13"), factory, candidates=(9600,), clock=clock)

    assert result.best_baudrate is None
    assert "Dispare um resultado" in result.message


def test_unreadable_in_all_baudrates_does_not_pick_any(clock: FakeClock) -> None:
    garbage = bytes([0xFF, 0x80, 0x98]) * 20
    factory = make_factory(clock, {9600: garbage, 4800: garbage})

    result = detect_baudrate(
        SerialSettings(port="COM13"), factory, candidates=(9600, 4800), clock=clock
    )

    assert result.best_baudrate is None
    assert "ilegíveis" in result.message


def test_open_failure_is_recorded_as_score_error(clock: FakeClock) -> None:
    factory = make_factory(clock, {9600: b"OK\n" * 10}, failing={19200})

    result = detect_baudrate(
        SerialSettings(port="COM13"), factory, candidates=(19200, 9600), clock=clock
    )

    assert result.best_baudrate == 9600
    assert result.scores[0].error is not None
    assert result.to_dict()["scores"][0]["baudrate"] == 19200
