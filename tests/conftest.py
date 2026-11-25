"""Shared fixtures for signed HTTP requests against isolated SQLite."""

from collections.abc import Iterator
from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.passwords import hash_password
from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import create_session_factory, get_db_session
from app.main import app


@pytest.fixture
def sessions() -> Iterator[sessionmaker[Session]]:
    # StaticPool keeps this single in-memory database across sequential requests.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            Base.metadata.create_all(connection)
            connection.commit()
        yield create_session_factory(engine)
    finally:
        engine.dispose()


@pytest.fixture
def client(sessions, monkeypatch) -> Iterator[TestClient]:
    def request_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_db_session, request_session)
    monkeypatch.setenv("JWT_SECRET_KEY", token_urlsafe(48))
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    get_settings.cache_clear()
    try:
        with TestClient(app) as http_client:
            yield http_client
    finally:
        get_settings.cache_clear()


@pytest.fixture
def credentials(sessions) -> dict[str, str]:
    password = token_urlsafe(24)
    with sessions.begin() as session:
        reader = User(
            tenant=Tenant(name="First"),
            email="reader@example.test",
            password_hash=hash_password(password),
        )
        session.add(reader)
        session.flush()
        return {
            "tenant_id": reader.tenant_id,
            "email": reader.email,
            "password": password,
        }


@pytest.fixture
def reader_token(client, credentials) -> str:
    response = client.post("/auth/login", json=credentials)
    assert response.status_code == 200
    return response.json()["access_token"]
