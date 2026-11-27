from pydantic import BaseModel

from app.schemas.retrieval import SearchRequest


class RagRequest(SearchRequest):
    """Uses the same strict query and top_k contract as search."""


class Citation(BaseModel):
    document_id: str
    filename: str
    chunk_id: str
    chunk_index: int
    score: float


class RagResponse(BaseModel):
    answer: str
    citations: list[Citation]
    model: str
    latency_ms: float
    retrieval_ms: float
    generation_ms: float
