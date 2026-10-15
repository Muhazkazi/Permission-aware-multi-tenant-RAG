"""Tests for database-backed principal and role authorization helpers."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import (
    get_current_principal,
    get_current_tenant_principal,
    require_role,
)
from app.core.principal import AuthenticatedPrincipal
from app.core.tokens import AccessTokenClaims
from app.db.models import Role, Tenant, User
from app.services.auth_service import (
    PrincipalResolutionError,
    resolve_authenticated_principal,
)


class _UserAndTenantResult:
    """Minimal result object for an isolated principal-resolution test."""

    def __init__(self, value: tuple[User, Tenant] | None) -> None:
        self.value = value

    def one_or_none(self) -> tuple[User, Tenant] | None:
        return self.value


class _RolesResult:
    """Minimal scalar result object for an isolated principal-resolution test."""

    def __init__(self, roles: list[Role]) -> None:
        self.roles = roles

    def all(self) -> list[Role]:
        return self.roles


class _SessionDouble:
    """Session double that returns preconfigured authoritative database results."""

    def __init__(
        self, user_and_tenant: tuple[User, Tenant] | None, roles: list[Role]
    ) -> None:
        self.user_and_tenant = user_and_tenant
        self.roles = roles

    def execute(self, _statement: object) -> _UserAndTenantResult:
        return _UserAndTenantResult(self.user_and_tenant)

    def scalars(self, _statement: object) -> _RolesResult:
        return _RolesResult(self.roles)


class _FailingSessionDouble:
    """Session double that simulates a database failure."""

    def execute(self, _statement: object) -> _UserAndTenantResult:
        raise SQLAlchemyError("database unavailable")


def _claims() -> AccessTokenClaims:
    """Return already-verified claims used to test database resolution only."""

    now = datetime.now(timezone.utc)
    return AccessTokenClaims(
        subject="user-1",
        tenant_id="tenant-1",
        token_id="token-1",
        issued_at=now,
        expires_at=now + timedelta(minutes=15),
    )


def _user(*, active: bool = True, tenant_id: str = "tenant-1") -> User:
    """Build an in-memory user without persisting application data."""

    return User(
        id="user-1",
        tenant_id=tenant_id,
        email="user@example.test",
        password_hash="stored-hash",
        is_active=active,
    )


def _tenant() -> Tenant:
    """Build an in-memory tenant without persisting application data."""

    return Tenant(id="tenant-1", name="Test Tenant")


def test_principal_resolves_current_roles_from_database_memberships() -> None:
    """Roles come from the database result, never from token or request data."""

    role = Role(id="role-1", tenant_id="tenant-1", name="reader")
    session = _SessionDouble((_user(), _tenant()), [role])

    principal = resolve_authenticated_principal(session, _claims())  # type: ignore[arg-type]

    assert principal.user.id == "user-1"
    assert principal.tenant_id == "tenant-1"
    assert principal.role_names == frozenset({"reader"})


@pytest.mark.parametrize(
    ("user", "tenant"),
    [
        (_user(active=False), _tenant()),
        (_user(tenant_id="other-tenant"), _tenant()),
    ],
)
def test_disabled_user_or_invalid_tenant_membership_fails_closed(
    user: User, tenant: Tenant
) -> None:
    """Missing active status or tenant agreement must not produce a principal."""

    session = _SessionDouble((user, tenant), [])

    with pytest.raises(PrincipalResolutionError):
        resolve_authenticated_principal(session, _claims())  # type: ignore[arg-type]


def test_missing_user_invalid_role_membership_and_database_failure_fail_closed() -> (
    None
):
    """No record, cross-tenant roles, and database errors must all deny access."""

    with pytest.raises(PrincipalResolutionError):
        resolve_authenticated_principal(_SessionDouble(None, []), _claims())  # type: ignore[arg-type]

    cross_tenant_role = Role(id="role-1", tenant_id="other-tenant", name="reader")
    with pytest.raises(PrincipalResolutionError):
        resolve_authenticated_principal(
            _SessionDouble((_user(), _tenant()), [cross_tenant_role]), _claims()
        )  # type: ignore[arg-type]

    with pytest.raises(PrincipalResolutionError):
        resolve_authenticated_principal(_FailingSessionDouble(), _claims())  # type: ignore[arg-type]


def test_tenant_principal_and_role_dependencies_enforce_database_roles() -> None:
    """Tenant scope and role checks use the resolved principal."""

    principal = AuthenticatedPrincipal(
        user=_user(),
        tenant=_tenant(),
        roles=(Role(id="role-1", tenant_id="tenant-1", name="reader"),),
    )

    assert get_current_tenant_principal(principal) is principal
    assert require_role("reader")(principal) is principal
    with pytest.raises(HTTPException) as error:
        require_role("admin")(principal)
    assert error.value.status_code == 403


def test_missing_bearer_token_is_rejected_before_database_access() -> None:
    """The principal dependency must fail closed when no bearer token is present."""

    with pytest.raises(HTTPException) as error:
        get_current_principal(None, None)  # type: ignore[arg-type]
    assert error.value.status_code == 401
