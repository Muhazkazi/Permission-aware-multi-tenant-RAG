"""Authenticated principal data exposed to protected application code."""

from __future__ import annotations

from dataclasses import dataclass

from app.db.models import Role, Tenant, User


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """A verified active user, their tenant, and roles resolved from the database."""

    user: User
    tenant: Tenant
    roles: tuple[Role, ...]

    @property
    def tenant_id(self) -> str:
        """Return the only tenant scope available to the current request."""

        return self.tenant.id

    @property
    def role_names(self) -> frozenset[str]:
        """Return current role names resolved from authoritative memberships."""

        return frozenset(role.name for role in self.roles)
