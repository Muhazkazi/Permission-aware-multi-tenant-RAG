"""SQLAlchemy engine and session helpers."""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def create_database_engine(database_url: str) -> Engine:
    """Create an engine without opening a database connection immediately."""

    return create_engine(database_url, pool_pre_ping=True)


def create_session_factory(database_engine: Engine) -> sessionmaker[Session]:
    """Create a factory that returns a new session for each caller."""

    return sessionmaker(
        bind=database_engine,
        autoflush=False,
        expire_on_commit=False,
    )


@lru_cache
def get_engine() -> Engine:
    """Return the configured engine, creating it only when database access is needed."""

    database_url = get_settings().database_url
    if not database_url:
        message = "DATABASE_URL must be configured before using the database."
        raise RuntimeError(message)

    return create_database_engine(database_url)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """Return the configured session factory."""

    return create_session_factory(get_engine())


def get_db_session() -> Generator[Session, None, None]:
    """Yield one database session and close it after the caller finishes."""

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
