from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def normalize_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def build_engine(url: str):
    normalized = normalize_url(url)
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if normalized.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(normalized, future=True, **kwargs)


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

