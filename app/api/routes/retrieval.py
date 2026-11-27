"""Authenticated ACL-filtered search, with SQL content hydration and auditing."""

from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant_principal
from app.core.principal import AuthenticatedPrincipal
from app.db.models import DocumentChunk, RetrievalAuditLog
from app.db.session import get_db_session
from app.schemas.retrieval import SearchHit, SearchRequest
from app.services.document_service import list_accessible_documents
from app.services.embeddings import Embeddings, get_embeddings
from app.services.vector_store import VectorStore, get_vector_store

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.post("/search", response_model=list[SearchHit])
def search(
    request: SearchRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_tenant_principal)],
    session: Annotated[Session, Depends(get_db_session)],
    store: Annotated[VectorStore, Depends(get_vector_store)],
    encoder: Annotated[Embeddings, Depends(get_embeddings)],
) -> list[SearchHit]:
    audit_fields = dict(
        tenant_id=principal.tenant_id,
        user_id=principal.user.id,
        query_hash=sha256(request.query.encode()).hexdigest(),
    )
    try:
        documents = list_accessible_documents(session, principal)
        by_id = {doc.id: doc for doc in documents}
        ranked = store.search(
            principal, documents, encoder.embed(request.query), request.top_k
        )
        chunks = (
            {
                c.qdrant_point_id: c
                for c in session.scalars(
                    select(DocumentChunk).where(
                        DocumentChunk.tenant_id == principal.tenant_id,
                        DocumentChunk.document_id.in_(by_id.keys()),
                        DocumentChunk.qdrant_point_id.in_([str(p.id) for p in ranked]),
                    )
                )
            }
            if ranked
            else {}
        )
        hits = []
        for point in ranked:
            chunk = chunks[str(point.id)]  # Fail closed on registry inconsistencies.
            doc = by_id[chunk.document_id]
            hits.append(
                SearchHit(
                    chunk_id=chunk.id,
                    document_id=doc.id,
                    chunk_index=chunk.chunk_index,
                    chunk_text=chunk.chunk_text,
                    score=point.score,
                    document_name=doc.name,
                    content_type=doc.content_type,
                )
            )
        session.add(
            RetrievalAuditLog(
                **audit_fields,
                outcome="success" if hits else "no_results",
                retrieved_chunk_ids=[h.chunk_id for h in hits],
            )
        )
        session.commit()
        return hits
    except Exception:
        session.rollback()
        try:
            session.add(
                RetrievalAuditLog(
                    **audit_fields, outcome="error", retrieved_chunk_ids=[]
                )
            )
            session.commit()
        except Exception:
            session.rollback()
        raise HTTPException(503, "Search is unavailable.") from None
