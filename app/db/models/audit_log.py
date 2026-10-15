"""Retrieval audit log ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.user import User


class RetrievalAuditLog(Base):
    """Append-only evidence of a retrieval attempt and its authorized results."""

    __tablename__ = "retrieval_audit_logs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_retrieval_audit_logs_tenant_user",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "outcome IN ('success', 'no_results', 'denied', 'error')",
            name="ck_retrieval_audit_logs_outcome",
        ),
        Index("ix_retrieval_audit_logs_tenant_created_at", "tenant_id", "created_at"),
        Index(
            "ix_retrieval_audit_logs_tenant_user_created_at",
            "tenant_id",
            "user_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    retrieved_chunk_ids: Mapped[list[str]] = mapped_column(
        JSON(), nullable=False, default=list
    )
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="retrieval_audit_logs")
