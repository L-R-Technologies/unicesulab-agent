from pathlib import Path

import pytest

from agent import database as db_status
from agent.database import LocalDatabase


@pytest.fixture
def local_db(tmp_path: Path) -> LocalDatabase:
    return LocalDatabase(tmp_path / "test.db")


def _insert(local_db: LocalDatabase, text: str) -> int:
    return local_db.insert_capture(
        text, text.encode().hex(), f"hash-{text}", "2026-10-28T10:00:00-03:00"
    )


def test_config_roundtrip(local_db: LocalDatabase) -> None:
    assert local_db.get_config("server_url") is None

    local_db.set_config("server_url", "https://a.com")
    assert local_db.get_config("server_url") == "https://a.com"

    local_db.set_config("server_url", "https://b.com")
    assert local_db.get_config("server_url") == "https://b.com"

    local_db.delete_config("server_url")
    assert local_db.get_config("server_url") is None


def test_insert_capture_is_idempotent_by_hash(local_db: LocalDatabase) -> None:
    first = _insert(local_db, "TP")
    second = _insert(local_db, "TP")

    assert first == second
    assert len(local_db.list_pending_captures()) == 1


def test_capture_status_transitions(local_db: LocalDatabase) -> None:
    capture_id = _insert(local_db, "TP")

    local_db.mark_capture_attempt(capture_id, 1, "offline")
    assert local_db.get_capture(capture_id)["status"] == db_status.CAPTURE_STATUS_CAPTURED
    assert local_db.get_capture(capture_id)["attempts"] == 1

    local_db.mark_capture_sent(capture_id, 42, 2)
    sent = local_db.get_capture(capture_id)
    assert sent["status"] == db_status.CAPTURE_STATUS_SENT
    assert sent["server_capture_id"] == 42
    assert sent["error_message"] is None
    assert local_db.list_pending_captures() == []


def test_reset_failed_captures_requeues_only_failed(local_db: LocalDatabase) -> None:
    failed_id = _insert(local_db, "A")
    sent_id = _insert(local_db, "B")
    local_db.mark_capture_failed(failed_id, 1, "422")
    local_db.mark_capture_sent(sent_id, 1, 1)

    assert local_db.reset_failed_captures() == 1
    assert local_db.get_capture(failed_id)["status"] == db_status.CAPTURE_STATUS_CAPTURED
    assert local_db.get_capture(failed_id)["error_message"] is None
    assert local_db.get_capture(sent_id)["status"] == db_status.CAPTURE_STATUS_SENT


def test_list_recent_captures_orders_by_id_desc(local_db: LocalDatabase) -> None:
    for text in ("A", "B", "C"):
        _insert(local_db, text)

    recent = local_db.list_recent_captures(limit=2)
    assert [capture["raw_text"] for capture in recent] == ["C", "B"]
