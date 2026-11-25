"""Schemas for the minimal authentication API."""

from pydantic import BaseModel, Field, SecretStr


class LoginRequest(BaseModel):
    """Tenant-scoped credentials submitted to the login endpoint."""

    tenant_id: str = Field(min_length=1, max_length=36)
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    """Safe response returned after successful credential verification."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class IdentityResponse(BaseModel):
    """Safe identity details derived from the current authenticated principal."""

    user_id: str
    tenant_id: str
    roles: list[str]
