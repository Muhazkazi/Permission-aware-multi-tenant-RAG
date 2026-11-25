"""Database-backed authentication helpers."""

from secrets import token_urlsafe

from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.passwords import hash_password, verify_password
from app.core.principal import AuthenticatedPrincipal
from app.core.tokens import AccessTokenClaims
from app.db.models import Role, Tenant, User, UserRole

_DUMMY_PASSWORD_HASH = hash_password(token_urlsafe(32))


class PrincipalResolutionError(ValueError):
    """Raised when a verified token cannot resolve to an authorized principal."""


class AuthenticationError(ValueError):
    """Raised when tenant-scoped credentials cannot authenticate a user."""


def authenticate_user(
    session: Session, *, tenant_id: str, email: str, password: str
) -> User:
    """Verify credentials against an active user without exposing failure details."""

    try:
        user = session.scalar(
            select(User).where(
                User.tenant_id == tenant_id,
                User.email == email,
                User.is_active.is_(True),
            )
        )
    except SQLAlchemyError as error:
        raise AuthenticationError("Invalid credentials.") from error

    if user is None or not user.is_active or user.tenant_id != tenant_id:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        raise AuthenticationError("Invalid credentials.")
    if not verify_password(password, user.password_hash):
        raise AuthenticationError("Invalid credentials.")
    return user


def resolve_authenticated_principal(
    session: Session, claims: AccessTokenClaims
) -> AuthenticatedPrincipal:
    """Resolve a verified token to a current active user, tenant, and role set."""

    try:
        user_and_tenant = session.execute(
            select(User, Tenant)
            .join(Tenant, Tenant.id == User.tenant_id)
            .where(
                User.id == claims.subject,
                User.tenant_id == claims.tenant_id,
                User.is_active.is_(True),
            )
        ).one_or_none()

        if user_and_tenant is None:
            raise PrincipalResolutionError("Unable to resolve authenticated principal.")

        user, tenant = user_and_tenant
        if (
            not user.is_active
            or user.tenant_id != tenant.id
            or tenant.id != claims.tenant_id
        ):
            raise PrincipalResolutionError("Unable to resolve authenticated principal.")

        roles = tuple(
            session.scalars(
                select(Role)
                .join(
                    UserRole,
                    and_(
                        UserRole.role_id == Role.id,
                        UserRole.tenant_id == Role.tenant_id,
                    ),
                )
                .where(
                    UserRole.user_id == user.id,
                    UserRole.tenant_id == tenant.id,
                    Role.tenant_id == tenant.id,
                )
                .order_by(Role.name)
            ).all()
        )
    except SQLAlchemyError as error:
        raise PrincipalResolutionError(
            "Unable to resolve authenticated principal."
        ) from error

    if any(role.tenant_id != tenant.id for role in roles):
        raise PrincipalResolutionError("Unable to resolve authenticated principal.")

    return AuthenticatedPrincipal(user=user, tenant=tenant, roles=roles)
