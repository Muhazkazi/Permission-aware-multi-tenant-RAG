"""Tests for database configuration that do not require MySQL."""

from sqlalchemy.engine import Engine

from app.core.config import Settings
from app.db.session import create_database_engine, create_session_factory


def test_settings_reads_database_url_from_environment(monkeypatch) -> None:
    """The database URL should come from DATABASE_URL, not application code."""

    database_url = "mysql+pymysql://test_user:test_password@localhost:3306/test_db"
    monkeypatch.setenv("DATABASE_URL", database_url)

    settings = Settings(_env_file=None)

    assert settings.database_url == database_url


def test_engine_and_session_factory_do_not_connect_during_setup() -> None:
    """Creating configuration objects should not require a running MySQL server."""

    database_url = "mysql+pymysql://test_user:test_password@localhost:3306/test_db"
    engine = create_database_engine(database_url)
    session_factory = create_session_factory(engine)

    first_session = session_factory()
    second_session = session_factory()
    try:
        assert isinstance(engine, Engine)
        assert engine.url.drivername == "mysql+pymysql"
        assert engine.url.username == "test_user"
        assert engine.url.password == "test_password"
        assert engine.url.host == "localhost"
        assert engine.url.database == "test_db"
        assert first_session is not second_session
    finally:
        first_session.close()
        second_session.close()
