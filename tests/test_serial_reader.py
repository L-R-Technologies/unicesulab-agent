from datetime import datetime, UTC

from agent.serial_reader import (
    CaptureFramer,
    build_capture,
    decode_live_text,
    extract_capture_preview,
    hash_capture_text,
    normalize_capture_text,
)
from tests.conftest import FakeClock, split_sample_blocks

IDLE_TIMEOUT = 3.0


def test_normalize_handles_machine_line_endings() -> None:
    raw = b"\n\nUNICESUMAR\n\r****   \n\rTEMPO:  16.9s\n\r\n\r\n"

    assert normalize_capture_text(raw) == "UNICESUMAR\n****\nTEMPO:  16.9s"


def test_normalize_handles_crlf_and_lone_cr() -> None:
    assert normalize_capture_text(b"a\r\nb\rc") == "a\nb\nc"


def test_hash_is_stable_for_same_text() -> None:
    assert hash_capture_text("abc") == hash_capture_text("abc")
    assert hash_capture_text("abc") != hash_capture_text("abd")


def test_build_capture_discards_blank_bytes() -> None:
    assert build_capture(b"\n\r   \n\r", datetime.now(UTC)) is None


def test_build_capture_keeps_raw_bytes_and_hash() -> None:
    captured_at = datetime(2026, 10, 28, 14, 3, 11, tzinfo=UTC)
    capture = build_capture(b"EXAME: TP\n\r", captured_at)

    assert capture is not None
    assert capture.text == "EXAME: TP"
    assert capture.raw_bytes_hex == b"EXAME: TP\n\r".hex()
    assert capture.raw_hash == hash_capture_text("EXAME: TP")
    assert capture.captured_at_iso == "2026-10-28T14:03:11+00:00"


def test_decode_live_text_drops_carriage_returns() -> None:
    assert decode_live_text(b"linha\n\routra\r\n") == "linha\noutra\n"


def test_extract_capture_preview_prefers_exam_line() -> None:
    text = "UNICESUMAR\n(0195)   CANAL 1\nEXAME:             TTPA\n   TTPA"
    assert extract_capture_preview(text) == "TTPA"


def test_extract_capture_preview_falls_back_to_first_line() -> None:
    assert extract_capture_preview("\n  primeira linha  \nsegunda") == "primeira linha"
    assert extract_capture_preview("") == ""


def test_framer_closes_capture_after_idle_timeout(clock: FakeClock) -> None:
    framer = CaptureFramer(IDLE_TIMEOUT, clock=clock)

    assert framer.feed(b"UNICE") == []
    clock.advance(1.0)
    assert framer.feed(b"SUMAR\n") == []
    assert framer.poll() is None

    clock.advance(IDLE_TIMEOUT - 0.5)
    assert framer.poll() is None

    clock.advance(1.0)
    assert framer.poll() == b"UNICESUMAR\n"
    assert framer.poll() is None
    assert not framer.has_pending_data


def test_framer_splits_by_max_bytes(clock: FakeClock) -> None:
    framer = CaptureFramer(IDLE_TIMEOUT, max_bytes=4, clock=clock)

    assert framer.feed(b"abcdefghij") == [b"abcd", b"efgh"]
    assert framer.flush() == b"ij"


def test_framer_flush_returns_none_when_empty(clock: FakeClock) -> None:
    assert CaptureFramer(IDLE_TIMEOUT, clock=clock).flush() is None


def test_fixture_produces_four_captures(clock: FakeClock, coagulometer_sample: bytes) -> None:
    blocks = split_sample_blocks(coagulometer_sample)
    assert len(blocks) == 4

    framer = CaptureFramer(IDLE_TIMEOUT, clock=clock)
    captures = []
    chunk_size = 16
    for block in blocks:
        for offset in range(0, len(block), chunk_size):
            captures.extend(framer.feed(block[offset : offset + chunk_size]))
            clock.advance(0.05)
        clock.advance(IDLE_TIMEOUT + 0.1)
        completed = framer.poll()
        if completed is not None:
            captures.append(completed)

    texts = [normalize_capture_text(raw) for raw in captures]
    assert len(texts) == 4
    assert all(text.startswith("UNICESUMAR") for text in texts)
    assert [extract_capture_preview(text) for text in texts] == ["TP", "TP", "TTPA", "FIB"]
    assert "CONC.:           339mg/dL" in texts[3]
    assert len({hash_capture_text(text) for text in texts}) == 4
