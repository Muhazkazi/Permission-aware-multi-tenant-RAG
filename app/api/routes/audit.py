from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.core.principal import AuthenticatedPrincipal
from app.db.models import Document, DocumentChunk, RetrievalAuditLog
from app.db.session import get_db_session

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEntry(BaseModel):
    timestamp: datetime
    request_id: str
    user_id: str
    action: str
    outcome: str
    chunk_ids: list[str]
    model: str | None


@router.get("/documents/{document_id}")
def document_access(
    document_id: str,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_role("admin"))],
    session: Annotated[Session, Depends(get_db_session)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AuditEntry]:
    tenant = principal.tenant_id
    if (
        session.scalar(
            select(Document.id).where(
                Document.id == document_id, Document.tenant_id == tenant
            )
        )
        is None
    ):
        raise HTTPException(404, "Document not found.")
    # Phase 8 entries only identify chunks. Resolve those using the tenant registry.
    legacy_ids = set(
        session.scalars(
            select(DocumentChunk.id).where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.tenant_id == tenant,
            )
        )
    )
    cutoff = session.scalar(select(func.current_timestamp())) - timedelta(days=days)
    rows = (
        select(RetrievalAuditLog)
        .where(
            RetrievalAuditLog.tenant_id == tenant,
            RetrievalAuditLog.created_at >= cutoff,
        )
        .order_by(RetrievalAuditLog.created_at.desc(), RetrievalAuditLog.id.desc())
    )
    result = []
    for row in session.scalars(rows.execution_options(yield_per=256)):
        details = row.details_json or {}
        if "documents" in details:
            chunks = details["documents"].get(document_id, [])
        else:
            chunks = [cid for cid in row.retrieved_chunk_ids if cid in legacy_ids]
        if not chunks:
            continue
        if offset:
            offset -= 1
            continue
        result.append(
            AuditEntry(
                timestamp=row.created_at,
                request_id=row.id,
                user_id=row.user_id,
                action=details.get("action", "retrieval.search"),
                outcome=row.outcome,
                chunk_ids=chunks,
                model=details.get("model"),
            )
        )
        if len(result) == limit:
            break
    return result
