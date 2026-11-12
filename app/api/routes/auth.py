"""Minimal login and identity endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal
from app.core.config import get_settings
from app.core.principal import AuthenticatedPrincipal
from app.core.tokens import TokenConfigurationError, create_access_token
from app.db.session import get_db_session
from app.schemas.auth import IdentityResponse, LoginRequest, TokenResponse
from app.services.auth_service import AuthenticationError, authenticate_user

router = APIRouter(prefix="/auth", tags=["authentication"])
INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid credentials.",
    headers={"WWW-Authenticate": "Bearer"},
)


@router.post("/login", response_model=TokenResponse)
def login(
    credentials: LoginRequest,
    session: Annotated[Session, Depends(get_db_session)],
) -> TokenResponse:
    """Verify tenant-scoped credentials and issue a short-lived access token."""

    try:
        user = authenticate_user(
            session,
            tenant_id=credentials.tenant_id,
            email=credentials.email,
            password=credentials.password,
        )
        access_token = create_access_token(user_id=user.id, tenant_id=user.tenant_id)
    except (AuthenticationError, TokenConfigurationError):
        raise INVALID_CREDENTIALS from None

    return TokenResponse(
        access_token=access_token,
        expires_in=get_settings().jwt_access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=IdentityResponse)
def get_identity(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
) -> IdentityResponse:
    """Return safe current identity data from database-resolved authorization state."""

    return IdentityResponse(
        user_id=principal.user.id,
        tenant_id=principal.tenant_id,
        roles=sorted(principal.role_names),
    )
