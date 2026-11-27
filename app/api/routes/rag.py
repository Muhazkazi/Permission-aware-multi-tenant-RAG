from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant_principal
from app.core.principal import AuthenticatedPrincipal
from app.db.session import get_db_session
from app.schemas.rag import Citation, RagRequest, RagResponse
from app.services.embeddings import Embeddings, get_embeddings
from app.services.llm import Provider, get_provider, grounded_answer
from app.services.retrieval import RetrievalError, retrieve
from app.services.vector_store import VectorStore, get_vector_store

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/query")
def query(
    request: RagRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_tenant_principal)],
    session: Annotated[Session, Depends(get_db_session)],
    store: Annotated[VectorStore, Depends(get_vector_store)],
    encoder: Annotated[Embeddings, Depends(get_embeddings)],
    provider: Annotated[Provider, Depends(get_provider)],
) -> RagResponse:
    start = perf_counter()
    try:
        hits = retrieve(
            request,
            principal,
            session,
            store,
            encoder,
            action="rag.query",
            model=provider.model,
        )
    except RetrievalError:
        raise HTTPException(503, "Search is unavailable.") from None
    ready = perf_counter()
    try:
        answer, used = grounded_answer(provider, request.query, hits)
    except Exception:
        raise HTTPException(503, "Answer generation is unavailable.") from None
    end = perf_counter()
    return RagResponse(
        answer=answer,
        model=provider.model,
        citations=[
            Citation(
                document_id=h.document_id,
                filename=h.document_name,
                chunk_id=h.chunk_id,
                chunk_index=h.chunk_index,
                score=h.score,
            )
            for h in used
        ],
        latency_ms=(end - start) * 1000,
        retrieval_ms=(ready - start) * 1000,
        generation_ms=(end - ready) * 1000,
    )
