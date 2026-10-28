from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FakeClock:
    """Relógio controlado pelos testes (substitui time.monotonic)."""

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


def split_sample_blocks(sample: bytes) -> list[bytes]:
    """Separa a fixture nos blocos impressos pela máquina (um por cabeçalho)."""
    header = b"UNICESUMAR"
    starts = []
    index = sample.find(header)
    while index != -1:
        starts.append(index)
        index = sample.find(header, index + len(header))
    ends = starts[1:] + [len(sample)]
    return [sample[start:end] for start, end in zip(starts, ends, strict=True)]
