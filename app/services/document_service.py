"""Tenant-scoped document metadata and access helpers."""

from uuid import uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.principal import AuthenticatedPrincipal
from app.db.models import Document, DocumentPermission


class DocumentServiceError(RuntimeError):
    """Raised when a document operation cannot complete safely."""


class DocumentConflictError(DocumentServiceError):
    """Raised when a document metadata insert violates a database constraint."""


class DocumentNotFoundError(DocumentServiceError):
    """Raised for a missing or inaccessible document without revealing why."""


def create_document_metadata(
    session: Session,
    principal: AuthenticatedPrincipal,
    *,
    name: str,
    content_type: str | None,
) -> Document:
    """Create metadata owned by the current user without creating file storage."""

    _validate_principal_scope(principal)
    document = Document(
        tenant_id=principal.tenant_id,
        owner_user_id=principal.user.id,
        name=name,
        content_type=content_type,
        storage_key=f"pending/{uuid4()}",
    )
    try:
        session.add(document)
        session.commit()
        session.refresh(document)
    except IntegrityError as error:
        session.rollback()
        raise DocumentConflictError("Unable to create document metadata.") from error
    except SQLAlchemyError as error:
        session.rollback()
        raise DocumentServiceError("Unable to create document metadata.") from error
    return document


def list_accessible_documents(
    session: Session, principal: AuthenticatedPrincipal
) -> list[Document]:
    """List only documents owned by or granted to the current tenant user."""

    _validate_principal_scope(principal)
    try:
        return list(
            session.scalars(
                select(Document)
                .where(
                    Document.tenant_id == principal.tenant_id,
                    _document_access_condition(principal),
                )
                .order_by(Document.created_at.desc(), Document.id.desc())
            ).all()
        )
    except SQLAlchemyError as error:
        raise DocumentServiceError("Unable to list documents.") from error


def get_accessible_document(
    session: Session, principal: AuthenticatedPrincipal, document_id: str
) -> Document:
    """Return one authorized document or a tenant-safe not-found result."""

    _validate_principal_scope(principal)
    try:
        document = session.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.tenant_id == principal.tenant_id,
                _document_access_condition(principal),
            )
        )
    except SQLAlchemyError as error:
        raise DocumentServiceError("Unable to read document.") from error

    if document is None:
        raise DocumentNotFoundError("Document not found.")
    return document


def _document_access_condition(principal: AuthenticatedPrincipal):
    """Build an owner-or-current-role access condition within one tenant."""

    owner_condition = Document.owner_user_id == principal.user.id
    role_ids = [
        role.id for role in principal.roles if role.tenant_id == principal.tenant_id
    ]
    if not role_ids:
        return owner_condition

    role_permission_exists = exists().where(
        DocumentPermission.tenant_id == principal.tenant_id,
        DocumentPermission.document_id == Document.id,
        DocumentPermission.role_id.in_(role_ids),
        DocumentPermission.permission == "read",
    )
    return or_(owner_condition, role_permission_exists)


def _validate_principal_scope(principal: AuthenticatedPrincipal) -> None:
    """Defensively reject an inconsistent principal before a data operation."""

    if principal.user.tenant_id != principal.tenant_id:
        raise DocumentServiceError("Unable to determine tenant scope.")
    if any(role.tenant_id != principal.tenant_id for role in principal.roles):
        raise DocumentServiceError("Unable to determine tenant scope.")
