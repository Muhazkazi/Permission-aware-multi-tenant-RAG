"""Tenant-scoped user-to-role membership ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.role import Role
    from app.db.models.user import User


class UserRole(Base):
    """Membership linking a user and role that belong to the same tenant."""

    __tablename__ = "user_roles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_user_roles_tenant_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.id"],
            name="fk_user_roles_tenant_role",
            ondelete="CASCADE",
        ),
        Index("ix_user_roles_tenant_role_id", "tenant_id", "role_id"),
    )

    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(primary_key=True)
    role_id: Mapped[str] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(
        back_populates="user_roles", overlaps="role,user_roles"
    )
    role: Mapped[Role] = relationship(
        back_populates="user_roles", overlaps="user,user_roles"
    )
