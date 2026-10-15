"""Document ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
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
    from app.db.models.document_chunk import DocumentChunk
    from app.db.models.document_permission import DocumentPermission
    from app.db.models.tenant import Tenant
    from app.db.models.user import User


class Document(Base):
    """Tenant-owned document metadata without document text or embeddings."""

    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "owner_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_documents_tenant_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "storage_key", name="uq_documents_tenant_storage_key"
        ),
        Index("ix_documents_tenant_owner_user_id", "tenant_id", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship(
        back_populates="documents", overlaps="owner,owned_documents"
    )
    owner: Mapped[User] = relationship(
        back_populates="owned_documents", overlaps="documents,tenant"
    )
    permissions: Mapped[list[DocumentPermission]] = relationship(
        back_populates="document", overlaps="document_permissions,role"
    )
    chunks: Mapped[list[DocumentChunk]] = relationship(back_populates="document")
