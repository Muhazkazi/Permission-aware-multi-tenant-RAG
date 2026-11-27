from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=4096)
    top_k: int = Field(default=5, ge=1, le=20, strict=True)


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    chunk_index: int
    score: float
    chunk_text: str
    document_name: str
    content_type: str | None
