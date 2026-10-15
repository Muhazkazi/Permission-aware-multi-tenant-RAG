"""Document permission ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.document import Document
    from app.db.models.role import Role


class DocumentPermission(Base):
    """A role's access grant for a document in the same tenant."""

    __tablename__ = "document_permissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_document_permissions_tenant_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.id"],
            name="fk_document_permissions_tenant_role",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "permission = 'read'", name="ck_document_permissions_read_only"
        ),
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "role_id",
            "permission",
            name="uq_document_permissions_tenant_document_role_permission",
        ),
        Index("ix_document_permissions_tenant_role_id", "tenant_id", "role_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role_id: Mapped[str] = mapped_column(String(36), nullable=False)
    permission: Mapped[str] = mapped_column(String(20), nullable=False, default="read")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=func.now()
    )

    document: Mapped[Document] = relationship(
        back_populates="permissions", overlaps="document_permissions,role"
    )
    role: Mapped[Role] = relationship(
        back_populates="document_permissions", overlaps="document,permissions"
    )
