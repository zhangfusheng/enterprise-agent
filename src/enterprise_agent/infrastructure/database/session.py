from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

# Wait this long for another writer instead of failing the commit at once.
_SQLITE_BUSY_TIMEOUT_SECONDS = 30


def normalize_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def build_engine(url: str):
    normalized = normalize_url(url)
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if normalized.startswith("sqlite"):
        kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": _SQLITE_BUSY_TIMEOUT_SECONDS,
        }
    engine = create_engine(normalized, future=True, **kwargs)
    if normalized.startswith("sqlite"):
        _configure_sqlite(engine)
    return engine


def _configure_sqlite(engine) -> None:
    """Keep a long read from blocking commits.

    The default rollback journal holds a shared lock until the reader finishes.
    A writer then fails at commit with ``database is locked``. WAL lets that
    commit proceed, and busy_timeout covers a short wait between writers.
    """

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={int(_SQLITE_BUSY_TIMEOUT_SECONDS * 1000)}")
        cursor.close()


def session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def session_scope(factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    with factory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

