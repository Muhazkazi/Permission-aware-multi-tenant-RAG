"""Schemas for tenant-scoped document metadata endpoints."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Document


class CreateDocumentRequest(BaseModel):
    """Safe metadata for a document record without file content or storage paths."""

    name: str = Field(min_length=1, max_length=255)
    content_type: str | None = Field(default=None, max_length=255)

    model_config = ConfigDict(str_strip_whitespace=True)


class DocumentResponse(BaseModel):
    """Safe document metadata exposed to an authorized tenant user."""

    document_id: str
    name: str
    content_type: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_document(cls, document: Document) -> "DocumentResponse":
        return cls(
            document_id=document.id,
            name=document.name,
            content_type=document.content_type,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )
