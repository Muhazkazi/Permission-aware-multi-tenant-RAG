"""Request-level tests for the minimal authentication API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.deps import get_current_principal
from app.api.routes import auth as auth_routes
from app.core.config import Settings, get_settings
from app.core.passwords import hash_password
from app.core.principal import AuthenticatedPrincipal
from app.core.tokens import create_access_token
from app.db.models import Role, Tenant, User
from app.db.session import get_db_session
from app.main import app

TEST_SECRET = "phase-five-test-secret-that-is-longer-than-thirty-two-characters"


class _LoginSessionDouble:
    """A session double that returns one configured user or no matching user."""

    def __init__(self, user: User | None) -> None:
        self.user = user

    def scalar(self, _statement: object) -> User | None:
        return self.user


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    """Keep FastAPI dependency overrides isolated between route tests."""

    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _active_user(
    *, tenant_id: str = "tenant-1", password: str = "correct-password"
) -> User:
    """Build an in-memory active user without adding a database record."""

    return User(
        id="user-1",
        tenant_id=tenant_id,
        email="reader@example.test",
        password_hash=hash_password(password),
        is_active=True,
    )


def _client_with_login_session(user: User | None) -> TestClient:
    """Return a client whose login route receives a non-persistent session double."""

    app.dependency_overrides[get_db_session] = lambda: _LoginSessionDouble(user)
    return TestClient(app)


def test_login_issues_an_access_token_after_successful_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid tenant, email, and password can receive an access token."""

    monkeypatch.setattr(auth_routes, "create_access_token", lambda **_kwargs: "token")
    monkeypatch.setattr(auth_routes, "get_settings", lambda: Settings(_env_file=None))
    client = _client_with_login_session(_active_user())

    response = client.post(
        "/auth/login",
        json={
            "tenant_id": "tenant-1",
            "email": "reader@example.test",
            "password": "correct-password",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "token",
        "token_type": "bearer",
        "expires_in": 900,
    }


@pytest.mark.parametrize(
    "user,password",
    [
        (None, "any-password"),
        (_active_user(), "incorrect-password"),
        (
            User(
                id="user-1",
                tenant_id="tenant-1",
                email="reader@example.test",
                password_hash=hash_password("correct-password"),
                is_active=False,
            ),
            "correct-password",
        ),
        (_active_user(tenant_id="other-tenant"), "correct-password"),
    ],
)
def test_login_rejects_unknown_wrong_disabled_and_tenant_mismatch_credentials(
    user: User | None, password: str
) -> None:
    """All credential failures produce the same generic response."""

    client = _client_with_login_session(user)

    response = client.post(
        "/auth/login",
        json={
            "tenant_id": "tenant-1",
            "email": "reader@example.test",
            "password": password,
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials."}


def test_identity_endpoint_returns_only_database_resolved_identity() -> None:
    """The identity response contains safe values from the resolved principal."""

    principal = AuthenticatedPrincipal(
        user=_active_user(),
        tenant=Tenant(id="tenant-1", name="Tenant One"),
        roles=(
            Role(id="role-1", tenant_id="tenant-1", name="reader"),
            Role(id="role-2", tenant_id="tenant-1", name="admin"),
        ),
    )
    app.dependency_overrides[get_current_principal] = lambda: principal

    response = TestClient(app).get("/auth/me")

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "roles": ["admin", "reader"],
    }


@pytest.mark.parametrize("token", [None, "not-a-valid-token"])
def test_identity_endpoint_rejects_missing_and_invalid_bearer_tokens(
    token: str | None,
) -> None:
    """No bearer token and malformed bearer tokens receive a generic 401."""

    app.dependency_overrides[get_db_session] = lambda: object()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    response = TestClient(app).get("/auth/me", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials."}


def test_identity_endpoint_rejects_an_expired_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired signed token must not reach database principal resolution."""

    monkeypatch.setenv("JWT_SECRET_KEY", TEST_SECRET)
    get_settings.cache_clear()
    expired_token = create_access_token(
        user_id="user-1",
        tenant_id="tenant-1",
        settings=Settings(jwt_secret_key=TEST_SECRET, _env_file=None),
        now=datetime.now(timezone.utc) - timedelta(minutes=16),
    )
    app.dependency_overrides[get_db_session] = lambda: object()

    response = TestClient(app).get(
        "/auth/me", headers={"Authorization": f"Bearer {expired_token}"}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials."}


def test_identity_roles_are_not_taken_from_token_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token role claim cannot alter database-resolved roles."""

    principal = AuthenticatedPrincipal(
        user=_active_user(),
        tenant=Tenant(id="tenant-1", name="Tenant One"),
        roles=(Role(id="role-1", tenant_id="tenant-1", name="reader"),),
    )
    monkeypatch.setattr(deps, "verify_access_token", lambda _token: object())
    monkeypatch.setattr(
        deps, "resolve_authenticated_principal", lambda *_args: principal
    )
    app.dependency_overrides[get_db_session] = lambda: object()

    response = TestClient(app).get(
        "/auth/me",
        headers={"Authorization": "Bearer signed-token-with-admin-role-claim"},
    )

    assert response.status_code == 200
    assert response.json()["roles"] == ["reader"]
