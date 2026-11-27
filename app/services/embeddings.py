"""Offline feature hashing and optional local sentence-transformer embeddings."""

import math
import re
from collections.abc import Callable, Sequence
from functools import lru_cache
from hashlib import blake2b

from app.core.config import get_settings


class Embeddings:
    def __init__(self, dimension: int, encoder: Callable[[str], Sequence[float]]):
        self.dimension = dimension
        self.encoder = encoder

    def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("Cannot embed empty text.")
        vector = list(map(float, self.encoder(text)))
        if len(vector) != self.dimension or not all(map(math.isfinite, vector)):
            raise ValueError("Invalid embedding dimensions or values.")
        return vector


def local_embeddings(dimension: int) -> Embeddings:
    def encode(text: str) -> list[float]:
        vector = [0.0] * dimension
        for term in re.findall(r"\w+|[^\w\s]", text.casefold()):
            digest = blake2b(term.encode(), digest_size=8).digest()
            vector[int.from_bytes(digest, "big") % dimension] += 1
        return vector

    return Embeddings(dimension, encode)


@lru_cache
def _sentence_model(model_name: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, local_files_only=True)


@lru_cache
def get_embeddings() -> Embeddings:
    settings = get_settings()
    if settings.embedding_provider == "local":
        return local_embeddings(settings.embedding_dimension)
    return Embeddings(
        settings.embedding_dimension,
        lambda text: _sentence_model(settings.embedding_model).encode(text),
    )
