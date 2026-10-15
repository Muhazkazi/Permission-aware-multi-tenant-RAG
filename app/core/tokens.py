"""JWT creation and verification for access tokens."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from jwt import InvalidTokenError

from app.core.config import Settings, get_settings


class TokenConfigurationError(ValueError):
    """Raised when token operations are attempted without secure configuration."""


class TokenValidationError(ValueError):
    """Raised when an access token cannot be validated safely."""


@dataclass(frozen=True)
class AccessTokenClaims:
    """Verified claims available to authentication code."""

    subject: str
    tenant_id: str
    token_id: str
    issued_at: datetime
    expires_at: datetime


def create_access_token(
    *,
    user_id: str,
    tenant_id: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> str:
    """Create a short-lived signed token with required identity claims."""

    if not user_id or not tenant_id:
        message = "A user ID and tenant ID are required to create an access token."
        raise ValueError(message)

    token_settings = settings or get_settings()
    secret = _get_jwt_secret(token_settings)
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(
        minutes=token_settings.jwt_access_token_expire_minutes
    )
    claims = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "iat": issued_at,
        "exp": expires_at,
        "jti": str(uuid4()),
    }
    return jwt.encode(claims, secret, algorithm=token_settings.jwt_algorithm)


def verify_access_token(
    token: str, settings: Settings | None = None
) -> AccessTokenClaims:
    """Verify a token signature and all required claims before using its identity."""

    token_settings = settings or get_settings()
    secret = _get_jwt_secret(token_settings)
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[token_settings.jwt_algorithm],
            options={"require": ["sub", "tenant_id", "exp", "iat", "jti"]},
        )
    except (InvalidTokenError, TypeError, ValueError) as error:
        raise TokenValidationError("Invalid access token.") from error

    subject = claims.get("sub")
    tenant_id = claims.get("tenant_id")
    token_id = claims.get("jti")
    if not all(
        isinstance(value, str) and value for value in (subject, tenant_id, token_id)
    ):
        raise TokenValidationError("Invalid access token.")

    issued_at = _timestamp_to_datetime(claims["iat"])
    expires_at = _timestamp_to_datetime(claims["exp"])
    return AccessTokenClaims(
        subject=subject,
        tenant_id=tenant_id,
        token_id=token_id,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def _get_jwt_secret(settings: Settings) -> str:
    """Translate configuration validation failures into a token-specific error."""

    try:
        return settings.require_jwt_secret()
    except ValueError as error:
        raise TokenConfigurationError("JWT configuration is invalid.") from error


def _timestamp_to_datetime(value: object) -> datetime:
    """Convert a verified numeric JWT timestamp to a timezone-aware datetime."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TokenValidationError("Invalid access token.")
    return datetime.fromtimestamp(value, tz=timezone.utc)
