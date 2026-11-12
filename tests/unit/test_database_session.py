import sqlite3
import time

from sqlalchemy import text

from enterprise_agent.infrastructure.database.session import build_engine


def test_sqlite_uses_wal_and_waits_for_a_busy_database(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path}/app.sqlite")

    with engine.connect() as connection:
        mode = connection.execute(text("PRAGMA journal_mode")).scalar()
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar()

    assert mode == "wal"
    assert busy_timeout == 30_000
    engine.dispose()


def test_sqlite_commit_is_not_blocked_by_an_open_reader(tmp_path):
    database = tmp_path / "app.sqlite"
    engine = build_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO items DEFAULT VALUES"))

    reader = sqlite3.connect(database)
    reader.execute("BEGIN")
    reader.execute("SELECT id FROM items")
    started = time.perf_counter()
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO items DEFAULT VALUES"))
    finally:
        reader.close()
        engine.dispose()

    assert time.perf_counter() - started < 2
