from agent import database as db_status
from agent.database import LocalDatabase
from tests.conftest import add_connection, insert_capture


def test_config_roundtrip(local_db: LocalDatabase) -> None:
    assert local_db.get_config("server_url") is None

    local_db.set_config("server_url", "https://a.com")
    assert local_db.get_config("server_url") == "https://a.com"

    local_db.set_config("server_url", "https://b.com")
    assert local_db.get_config("server_url") == "https://b.com"

    local_db.delete_config("server_url")
    assert local_db.get_config("server_url") is None


def test_connection_crud(local_db: LocalDatabase) -> None:
    first = add_connection(local_db, name="A", port="COM1")
    second = add_connection(local_db, name="B", port="COM2", enabled=False)

    rows = local_db.list_connections()
    assert [row["id"] for row in rows] == [first, second]
    assert rows[0]["rtscts"] == 1
    assert rows[1]["enabled"] == 0

    fields = dict(local_db.get_connection(first))
    fields["baudrate"] = 9600
    local_db.update_connection(first, fields)
    assert local_db.get_connection(first)["baudrate"] == 9600

    local_db.delete_connection(first)
    assert local_db.get_connection(first) is None
    assert [row["id"] for row in local_db.list_connections()] == [second]


def test_deleting_connection_removes_its_captures(local_db: LocalDatabase) -> None:
    connection_id = add_connection(local_db)
    other_id = add_connection(local_db, name="Outra", port="COM2")
    insert_capture(local_db, connection_id, "A")
    kept = insert_capture(local_db, other_id, "B")

    local_db.delete_connection(connection_id)

    assert [capture["id"] for capture in local_db.list_pending_captures()] == [kept]


def test_insert_capture_is_idempotent_per_connection(local_db: LocalDatabase) -> None:
    connection_id = add_connection(local_db)
    other_id = add_connection(local_db, name="Outra", port="COM2")

    first = insert_capture(local_db, connection_id, "TP")
    second = insert_capture(local_db, connection_id, "TP")
    third = insert_capture(local_db, other_id, "TP")

    assert first == second
    assert third != first
    assert len(local_db.list_pending_captures(connection_id)) == 1
    assert len(local_db.list_pending_captures()) == 2


def test_capture_status_transitions(local_db: LocalDatabase) -> None:
    capture_id = insert_capture(local_db, add_connection(local_db), "TP")

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
    connection_id = add_connection(local_db)
    failed_id = insert_capture(local_db, connection_id, "A")
    sent_id = insert_capture(local_db, connection_id, "B")
    local_db.mark_capture_failed(failed_id, 1, "422")
    local_db.mark_capture_sent(sent_id, 1, 1)

    assert local_db.reset_failed_captures() == 1
    assert local_db.get_capture(failed_id)["status"] == db_status.CAPTURE_STATUS_CAPTURED
    assert local_db.get_capture(failed_id)["error_message"] is None
    assert local_db.get_capture(sent_id)["status"] == db_status.CAPTURE_STATUS_SENT


def test_list_recent_captures_includes_connection_name(local_db: LocalDatabase) -> None:
    connection_id = add_connection(local_db, name="Bancada 2")
    for text in ("A", "B", "C"):
        insert_capture(local_db, connection_id, text)

    recent = local_db.list_recent_captures(limit=2)
    assert [capture["raw_text"] for capture in recent] == ["C", "B"]
    assert recent[0]["connection_name"] == "Bancada 2"
