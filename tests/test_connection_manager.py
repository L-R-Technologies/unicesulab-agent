import threading

from agent.connection_manager import ConnectionManager
from agent.database import LocalDatabase
from agent.state import SERIAL_DISABLED, AgentState
from tests.conftest import add_connection


class FakeWorker:
    def __init__(self, connection_id: int) -> None:
        self.connection_id = connection_id
        self.started = False
        self.stopped = False
        self._alive = False

    def start(self) -> None:
        self.started = True
        self._alive = True

    def stop(self) -> None:
        self.stopped = True
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive


def make_manager(local_db: LocalDatabase) -> tuple[ConnectionManager, AgentState, list]:
    state = AgentState()
    created: list[FakeWorker] = []

    def factory(connection_id: int) -> FakeWorker:
        worker = FakeWorker(connection_id)
        created.append(worker)
        return worker

    return ConnectionManager(local_db, state, worker_factory=factory), state, created


def test_starts_a_worker_per_enabled_connection(local_db: LocalDatabase) -> None:
    first = add_connection(local_db, name="A", port="COM1")
    add_connection(local_db, name="B", port="COM2", enabled=False)
    manager, state, created = make_manager(local_db)

    manager.reconcile()

    assert manager.running_connection_ids() == [first]
    assert created[0].started
    disabled_id = local_db.list_connections()[1]["id"]
    assert state.connection(disabled_id).snapshot()["serial_status"] == SERIAL_DISABLED


def test_stops_worker_when_connection_is_removed_or_disabled(local_db: LocalDatabase) -> None:
    first = add_connection(local_db, name="A", port="COM1")
    second = add_connection(local_db, name="B", port="COM2")
    manager, state, created = make_manager(local_db)
    manager.reconcile()
    state.connection(first)
    assert manager.running_connection_ids() == [first, second]

    local_db.delete_connection(first)
    fields = dict(local_db.get_connection(second))
    fields["enabled"] = 0
    local_db.update_connection(second, fields)
    manager.reconcile()

    assert manager.running_connection_ids() == []
    assert all(worker.stopped for worker in created)
    assert first not in state.connection_ids()
    assert second in state.connection_ids()


def test_config_change_signals_the_running_worker(local_db: LocalDatabase) -> None:
    connection_id = add_connection(local_db)
    manager, state, created = make_manager(local_db)
    manager.reconcile()
    assert not state.connection(connection_id).config_changed.is_set()

    fields = dict(local_db.get_connection(connection_id))
    fields["baudrate"] = 9600
    local_db.update_connection(connection_id, fields)
    manager.reconcile()

    assert state.connection(connection_id).config_changed.is_set()
    assert len(created) == 1


def test_dead_worker_is_restarted(local_db: LocalDatabase) -> None:
    add_connection(local_db)
    manager, _, created = make_manager(local_db)
    manager.reconcile()
    created[0]._alive = False

    manager.reconcile()

    assert len(created) == 2
    assert created[1].started


def test_run_loop_reconciles_and_stops_everything(local_db: LocalDatabase) -> None:
    add_connection(local_db)
    manager, state, created = make_manager(local_db)

    manager.start()
    state.connections_changed.set()
    deadline = threading.Event()
    deadline.wait(0.2)
    manager.stop()
    manager.join(timeout=2)

    assert not manager.is_alive()
    assert created[0].started
    assert created[0].stopped
