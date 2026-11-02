import sqlite3

import pytest

from enterprise_agent.infrastructure.checkpoints.store import CheckpointFactory


def test_sqlite_open_creates_parent_and_closes_connection(tmp_path):
    path = tmp_path / "nested" / "checkpoint.sqlite"
    factory = CheckpointFactory(f"sqlite:///{path}")

    with factory.open() as saver:
        connection = saver.conn
        assert path.is_file()
        assert saver.get_tuple({"configurable": {"thread_id": "run-1"}}) is None

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("select 1")


def test_sqlite_setup_failure_closes_connection(tmp_path, monkeypatch):
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def spy(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    def fail(self):
        raise RuntimeError("setup failed")

    monkeypatch.setattr(sqlite3, "connect", spy)
    monkeypatch.setattr("langgraph.checkpoint.sqlite.SqliteSaver.setup", fail)

    with pytest.raises(RuntimeError, match="setup failed"):
        with CheckpointFactory(f"sqlite:///{tmp_path / 'checkpoint.sqlite'}").open():
            pass

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("select 1")


def test_unsupported_checkpoint_url():
    with pytest.raises(ValueError, match="unsupported checkpoint URL"):
        with CheckpointFactory("mysql://localhost/checkpoints").open():
            pass
