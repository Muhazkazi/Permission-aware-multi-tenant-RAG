"""Authenticated ACL-filtered search, with SQL content hydration and auditing."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant_principal
from app.core.principal import AuthenticatedPrincipal
from app.db.session import get_db_session
from app.schemas.retrieval import SearchHit, SearchRequest
from app.services.embeddings import Embeddings, get_embeddings
from app.services.retrieval import RetrievalError, retrieve
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
    try:
        return retrieve(request, principal, session, store, encoder)
    except RetrievalError:
        raise HTTPException(503, "Search is unavailable.") from None
