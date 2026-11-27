"""Qdrant stores vectors and authorization snapshots. Content stays in SQL."""

from functools import lru_cache
from threading import Lock

from qdrant_client import QdrantClient
from qdrant_client import models as m

from app.core.config import get_settings
from app.core.principal import AuthenticatedPrincipal
from app.db.models import Document, DocumentChunk
from app.services.embeddings import Embeddings


def equals(key: str, value: str) -> m.FieldCondition:
    return m.FieldCondition(key=key, match=m.MatchValue(value=value))


def document_filter(tenant_id: str, document_id: str) -> m.Filter:
    if not tenant_id.strip() or not document_id.strip():
        raise ValueError("A tenant and document are required.")
    return m.Filter(
        must=[equals("tenant_id", tenant_id), equals("document_id", document_id)]
    )


def acl_payload(doc: Document) -> dict:
    return {
        "allowed_roles": sorted(
            {
                p.role_id
                for p in doc.permissions
                if p.tenant_id == doc.tenant_id and p.permission == "read"
            }
        ),
        "allowed_users": [doc.owner_user_id],
        "classification": "restricted",
    }


class VectorStore:
    def __init__(self, client: QdrantClient, collection: str, dimension: int):
        self.client = client
        self.collection = collection
        self.dimension = dimension
        self._lock = Lock()
        self._ready = False

    def ensure_collection(self) -> None:
        with self._lock:
            if self._ready:
                return
            if not self.client.collection_exists(self.collection):
                try:
                    self.client.create_collection(
                        self.collection,
                        vectors_config=m.VectorParams(
                            size=self.dimension, distance=m.Distance.COSINE
                        ),
                    )
                except Exception:
                    if not self.client.collection_exists(self.collection):
                        raise
            info = self.client.get_collection(self.collection)
            params = info.config.params.vectors
            if not isinstance(params, m.VectorParams) or (
                params.size != self.dimension or params.distance != m.Distance.COSINE
            ):
                raise ValueError("Incompatible vector collection.")
            for key in ("tenant_id", "allowed_roles", "allowed_users", "document_id"):
                self.client.create_payload_index(
                    self.collection, key, m.PayloadSchemaType.KEYWORD, wait=True
                )
            self._ready = True

    def upsert_document(
        self, doc: Document, chunks: list[DocumentChunk], encoder: Embeddings
    ) -> None:
        if (
            not doc.tenant_id
            or not doc.tenant_id.strip()
            or not doc.id
            or not doc.id.strip()
        ):
            raise ValueError("Missing document scope.")
        if any(c.tenant_id != doc.tenant_id or c.document_id != doc.id for c in chunks):
            raise ValueError("Invalid chunk scope.")
        self.ensure_collection()
        acl = acl_payload(doc)
        for start in range(0, len(chunks), 64):
            points = [
                m.PointStruct(
                    id=c.qdrant_point_id,
                    vector=encoder.embed(c.chunk_text or ""),
                    payload={
                        **acl,
                        "tenant_id": doc.tenant_id,
                        "document_id": doc.id,
                        "chunk_id": c.id,
                        "chunk_index": c.chunk_index,
                    },
                )
                for c in chunks[start : start + 64]
            ]
            self.client.upsert(self.collection, points, wait=True)

    def sync_document_acl(self, doc: Document) -> None:
        self.ensure_collection()
        self.client.set_payload(
            self.collection,
            acl_payload(doc),
            document_filter(doc.tenant_id, doc.id),
            wait=True,
        )

    def delete_document(self, tenant_id: str, document_id: str) -> None:
        self.client.delete(
            self.collection, document_filter(tenant_id, document_id), wait=True
        )

    def search(
        self,
        principal: AuthenticatedPrincipal,
        documents: list[Document],
        query: list[float],
        limit: int,
    ):
        if not documents:
            return []
        self.ensure_collection()
        tenant = principal.tenant_id
        access = [equals("allowed_users", principal.user.id)]
        if principal.roles:
            access.append(
                m.FieldCondition(
                    key="allowed_roles",
                    match=m.MatchAny(any=[role.id for role in principal.roles]),
                )
            )
        # SQL authorizes the candidate documents. Repair missing grants in the
        # vector snapshot before querying so a newly granted reader sees them.
        # The document allowlist also excludes revoked grants and orphan points.
        candidates = [
            equals("tenant_id", tenant),
            m.FieldCondition(
                key="document_id", match=m.MatchAny(any=[doc.id for doc in documents])
            ),
        ]
        drift, _ = self.client.scroll(
            self.collection,
            scroll_filter=m.Filter(must=candidates, must_not=[m.Filter(should=access)]),
            limit=1,
            with_payload=False,
            with_vectors=False,
        )
        if drift:
            for doc in documents:
                selector = document_filter(tenant, doc.id)
                selector.must_not = [m.Filter(should=access)]
                self.client.set_payload(
                    self.collection, acl_payload(doc), selector, wait=True
                )
        scope = m.Filter(must=candidates, should=access)
        return self.client.query_points(
            self.collection,
            query=query,
            query_filter=scope,
            limit=limit,
            with_payload=False,
        ).points


@lru_cache
def get_vector_store() -> VectorStore:
    settings = get_settings()
    return VectorStore(
        QdrantClient(url=settings.qdrant_url, check_compatibility=False),
        settings.qdrant_collection_name,
        settings.embedding_dimension,
    )
