"""Role ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.document_permission import DocumentPermission
    from app.db.models.tenant import Tenant
    from app.db.models.user_role import UserRole


class Role(Base):
    """A named role available only within one tenant."""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_id_name"),
        UniqueConstraint("tenant_id", "id", name="uq_roles_tenant_id_id"),
        Index("ix_roles_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="roles")
    user_roles: Mapped[list[UserRole]] = relationship(
        back_populates="role", overlaps="user,user_roles"
    )
    document_permissions: Mapped[list[DocumentPermission]] = relationship(
        back_populates="role", overlaps="document,document_permissions,permissions"
    )
