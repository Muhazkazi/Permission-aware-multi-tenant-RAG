"""Tests for JWT configuration, creation, and validation."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.tokens import (
    TokenConfigurationError,
    TokenValidationError,
    create_access_token,
    verify_access_token,
)

TEST_SECRET = "test-secret-that-is-longer-than-thirty-two-characters"
OTHER_TEST_SECRET = "other-test-secret-that-is-longer-than-thirty-two-characters"


def _settings(secret: str = TEST_SECRET) -> Settings:
    """Create isolated test settings without reading a local .env file."""

    return Settings(jwt_secret_key=secret, _env_file=None)


def test_access_token_contains_and_validates_required_claims() -> None:
    """A signed token should resolve only its required verified claims."""

    token = create_access_token(
        user_id="user-1",
        tenant_id="tenant-1",
        settings=_settings(),
    )

    claims = verify_access_token(token, settings=_settings())

    assert claims.subject == "user-1"
    assert claims.tenant_id == "tenant-1"
    assert claims.token_id
    assert claims.issued_at.tzinfo == timezone.utc
    assert claims.expires_at > claims.issued_at


def test_expired_and_invalidly_signed_tokens_are_rejected() -> None:
    """Expiration and signature validation must fail closed."""

    settings = _settings()
    now = datetime.now(timezone.utc)
    expired_token = jwt.encode(
        {
            "sub": "user-1",
            "tenant_id": "tenant-1",
            "jti": "token-1",
            "iat": now - timedelta(minutes=2),
            "exp": now - timedelta(minutes=1),
        },
        TEST_SECRET,
        algorithm=settings.jwt_algorithm,
    )
    valid_token = create_access_token(
        user_id="user-1",
        tenant_id="tenant-1",
        settings=settings,
    )

    with pytest.raises(TokenValidationError):
        verify_access_token(expired_token, settings=settings)
    with pytest.raises(TokenValidationError):
        verify_access_token(valid_token, settings=_settings(OTHER_TEST_SECRET))
    with pytest.raises(TokenValidationError):
        verify_access_token("not-a-jwt", settings=settings)


def test_token_missing_a_required_claim_is_rejected() -> None:
    """Tokens must provide all identity, expiry, issue-time, and ID claims."""

    settings = _settings()
    now = datetime.now(timezone.utc)
    token_without_token_id = jwt.encode(
        {
            "sub": "user-1",
            "tenant_id": "tenant-1",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        TEST_SECRET,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(TokenValidationError):
        verify_access_token(token_without_token_id, settings=settings)


def test_missing_or_weak_jwt_secret_is_not_usable() -> None:
    """Token creation must reject missing and short signing secrets."""

    with pytest.raises(TokenConfigurationError):
        create_access_token(
            user_id="user-1",
            tenant_id="tenant-1",
            settings=Settings(_env_file=None),
        )
    with pytest.raises(TokenConfigurationError):
        create_access_token(
            user_id="user-1",
            tenant_id="tenant-1",
            settings=Settings(jwt_secret_key="too-short", _env_file=None),
        )


def test_production_settings_require_a_strong_jwt_secret(monkeypatch) -> None:
    """Production configuration should fail before an app can use weak JWT settings."""

    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError):
        Settings(app_env="production", _env_file=None)
