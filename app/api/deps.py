"""Reusable FastAPI dependencies for authentication and role authorization."""

from typing import Annotated, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.principal import AuthenticatedPrincipal
from app.core.tokens import (
    TokenConfigurationError,
    TokenValidationError,
    verify_access_token,
)
from app.db.session import get_db_session
from app.services.auth_service import (
    PrincipalResolutionError,
    resolve_authenticated_principal,
)

bearer_scheme = HTTPBearer(auto_error=False)


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[Session, Depends(get_db_session)],
) -> AuthenticatedPrincipal:
    """Return the principal derived from a verified token and database state."""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthenticated()

    try:
        claims = verify_access_token(credentials.credentials)
        return resolve_authenticated_principal(session, claims)
    except (TokenConfigurationError, TokenValidationError, PrincipalResolutionError):
        raise _unauthenticated() from None


def get_current_tenant_principal(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
) -> AuthenticatedPrincipal:
    """Expose the authenticated principal as the request's tenant scope."""

    return principal


def require_role(
    role_name: str,
) -> Callable[[AuthenticatedPrincipal], AuthenticatedPrincipal]:
    """Create a dependency that requires a database-resolved tenant role."""

    def role_dependency(
        principal: Annotated[
            AuthenticatedPrincipal, Depends(get_current_tenant_principal)
        ],
    ) -> AuthenticatedPrincipal:
        if role_name not in principal.role_names:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role.",
            )
        return principal

    return role_dependency
