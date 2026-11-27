"""Authenticated ACL-filtered search, with SQL content hydration and auditing."""

from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.principal import AuthenticatedPrincipal
from app.db.models import DocumentChunk, RetrievalAuditLog
from app.schemas.retrieval import SearchHit, SearchRequest
from app.services.document_service import list_accessible_documents
from app.services.embeddings import Embeddings
from app.services.vector_store import VectorStore


class RetrievalError(RuntimeError):
    """Retrieval or its audit could not be completed."""


def retrieve(
    request: SearchRequest,
    principal: AuthenticatedPrincipal,
    session: Session,
    store: VectorStore,
    encoder: Embeddings,
    *,
    action: str = "retrieval.search",
    model: str | None = None,
) -> list[SearchHit]:
    audit_fields = dict(
        tenant_id=principal.tenant_id,
        user_id=principal.user.id,
        details_json={"action": action, "model": model},
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
        audit_fields["details_json"]["documents"] = {
            doc_id: [h.chunk_id for h in hits if h.document_id == doc_id]
            for doc_id in {h.document_id for h in hits}
        }
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
        audit_fields["details_json"].pop("documents", None)
        try:
            session.add(
                RetrievalAuditLog(
                    **audit_fields, outcome="error", retrieved_chunk_ids=[]
                )
            )
            session.commit()
        except Exception:
            session.rollback()
        raise RetrievalError from None
