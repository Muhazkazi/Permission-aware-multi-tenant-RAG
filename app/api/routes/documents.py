"""Tenant-scoped document metadata endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant_principal
from app.core.principal import AuthenticatedPrincipal
from app.db.models import Document
from app.db.session import get_db_session
from app.schemas.documents import CreateDocumentRequest, DocumentResponse
from app.services.document_service import (
    DocumentNotFoundError,
    DocumentServiceError,
    create_document_metadata,
    get_accessible_document,
    list_accessible_documents,
)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def create_document(
    request: CreateDocumentRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_tenant_principal)],
    session: Annotated[Session, Depends(get_db_session)],
) -> DocumentResponse:
    """Create metadata owned by the authenticated user in their tenant."""

    try:
        document = create_document_metadata(
            session,
            principal,
            name=request.name,
            content_type=request.content_type,
        )
        response = _document_response(document)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not create document.",
        ) from None
    except (DocumentServiceError, SQLAlchemyError, ValidationError):
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not process document request.",
        ) from None
    return response


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_tenant_principal)],
    session: Annotated[Session, Depends(get_db_session)],
) -> list[DocumentResponse]:
    """List documents currently authorized for the authenticated tenant user."""

    try:
        documents = list_accessible_documents(session, principal)
    except DocumentServiceError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not process document request.",
        ) from None
    return [_document_response(document) for document in documents]


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: str,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_tenant_principal)],
    session: Annotated[Session, Depends(get_db_session)],
) -> DocumentResponse:
    """Return one document only when it is accessible within the current tenant."""

    try:
        document = get_accessible_document(session, principal, document_id)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        ) from None
    except DocumentServiceError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not process document request.",
        ) from None
    return _document_response(document)


def _document_response(document: Document) -> DocumentResponse:
    """Map a model to the explicit, safe document response shape."""

    return DocumentResponse.from_document(document)
