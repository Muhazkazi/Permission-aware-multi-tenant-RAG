"""Signed requests, real SQL grants, and isolated Qdrant local mode."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.tokens import create_access_token
from app.db.models import (
    Document,
    DocumentChunk,
    DocumentPermission,
    RetrievalAuditLog,
    Role,
    Tenant,
    User,
    UserRole,
)
from app.services.embeddings import Embeddings, local_embeddings


@pytest.fixture
def authorized(client, reader_token, tmp_path, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    client.headers["Authorization"] = f"Bearer {reader_token}"
    return client


def upload(client, text="shared search phrase"):
    response = client.post(
        "/documents/upload",
        content=text.encode(),
        headers={"Content-Type": "text/plain", "X-Filename": "notes.txt"},
    )
    assert response.status_code == 201
    return response.json()["document_id"]


def actor(sessions, tenant_id=None):
    with sessions.begin() as session:
        tenant = (
            session.get_one(Tenant, tenant_id)
            if tenant_id
            else Tenant(name=str(uuid4()))
        )
        user = User(tenant=tenant, email="another@example.test", password_hash="unused")
        session.add(user)
        session.flush()
        return user.id, "Bearer " + create_access_token(
            user_id=user.id, tenant_id=user.tenant_id
        )


def search(client):
    response = client.post("/retrieval/search", json={"query": "search phrase"})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("field", ["document_id", "tenant_id"])
@pytest.mark.parametrize("missing", ["", None, " "])
def test_ingestion_guard(vector_store, field, missing):
    doc = Document(id="doc", tenant_id="tenant", owner_user_id="user")
    chunk = DocumentChunk(document_id=doc.id, tenant_id=doc.tenant_id)
    setattr(chunk, field, missing)
    with pytest.raises(ValueError):
        vector_store.upsert_document(doc, [chunk], local_embeddings(32))
    assert not vector_store.client.collection_exists(vector_store.collection)


def test_embeddings():
    encoder = local_embeddings(32)
    assert encoder.embed("same text") == local_embeddings(32).embed("same text")
    assert len(encoder.embed("same text")) == 32
    with pytest.raises(ValueError):
        encoder.embed("  ")
    for invalid in ([1], [float("nan"), 0]):
        with pytest.raises(ValueError):
            Embeddings(2, lambda text: invalid).embed("hello")


def test_cross_tenant_filter_and_safe_results(authorized, sessions, vector_store):
    client = authorized
    doc_a = upload(client)
    assert search(client)[0]["document_id"] == doc_a
    user_b, token = actor(sessions)
    client.headers["Authorization"] = token
    doc_b = upload(client)
    with patch.object(
        vector_store.client, "query_points", wraps=vector_store.client.query_points
    ) as call:
        response = client.post(
            "/retrieval/search?tenant_id=spoof&roles=admin&user_id=spoof",
            json={"query": "shared search phrase"},
        )
    assert response.status_code == 200
    (hit,) = response.json()
    assert hit["document_id"] == doc_b
    assert hit["chunk_text"] == "shared search phrase"
    assert set(hit) == {
        "document_id",
        "chunk_id",
        "chunk_index",
        "score",
        "chunk_text",
        "document_name",
        "content_type",
    }
    scope = call.call_args.kwargs["query_filter"]
    with sessions() as session:
        assert scope.must[0].match.value == session.get_one(User, user_b).tenant_id
        assert scope.should[0].match.value == user_b
        log = session.scalars(
            select(RetrievalAuditLog).where(RetrievalAuditLog.user_id == user_b)
        ).one()
        assert log.retrieved_chunk_ids == [hit["chunk_id"]]


def test_current_role_grant_and_revocation(
    authorized, sessions, credentials, vector_store
):
    client = authorized
    doc_id = upload(client)
    user_id, token = actor(sessions, credentials["tenant_id"])
    client.headers["Authorization"] = token
    assert search(client) == []
    with sessions.begin() as session:
        tenant = session.get_one(Tenant, credentials["tenant_id"])
        role = Role(tenant=tenant, name="reader")
        grant = DocumentPermission(
            document=session.get_one(Document, doc_id), role=role
        )
        session.add_all(
            [UserRole(user=session.get_one(User, user_id), role=role), grant]
        )
    assert search(client)[0]["document_id"] == doc_id
    points, _ = vector_store.client.scroll(vector_store.collection)
    payload = points[0].payload
    assert payload["allowed_roles"] == [role.id]
    assert payload["classification"] == "restricted"
    assert payload["tenant_id"] == credentials["tenant_id"]
    assert "chunk_text" not in payload
    # Keep a visible document so the next search really executes in Qdrant.
    owned = upload(client)
    with sessions.begin() as session:
        session.delete(session.get_one(DocumentPermission, grant.id))
    assert [h["document_id"] for h in search(client)] == [owned]


@pytest.mark.parametrize(
    "body",
    [
        {"query": " "},
        {"query": "x", "roles": ["admin"]},
        {"query": "x", "tenant_id": "other"},
        {"query": "x", "user_id": "other"},
        {"query": "x", "top_k": 21},
        {"query": "x", "top_k": 0},
    ],
)
def test_search_validation(authorized, body):
    assert authorized.post("/retrieval/search", json=body).status_code == 422


def test_missing_auth(client):
    assert client.post("/retrieval/search", json={"query": "words"}).status_code == 401


def test_partial_index_failure_rolls_back_all_stores(
    authorized, sessions, vector_store, monkeypatch, tmp_path
):
    upsert = vector_store.client.upsert

    def fail_after_write(*args, **kwargs):
        upsert(*args, **kwargs)
        raise RuntimeError("internal")

    monkeypatch.setattr(vector_store.client, "upsert", fail_after_write)
    response = authorized.post(
        "/documents/upload",
        content=b"partial index",
        headers={"X-Filename": "x.txt", "Content-Type": "text/plain"},
    )
    assert response.status_code == 500
    with sessions() as session:
        assert session.scalar(select(Document.id)) is None
        assert session.scalar(select(DocumentChunk.id)) is None
    assert not any(p.is_file() for p in tmp_path.rglob("*"))
    assert vector_store.client.count(vector_store.collection).count == 0


def test_top_k_is_applied_inside_ranked_search(authorized):
    best = upload(authorized, "forecast")
    upload(authorized, "forecast expenses")
    response = authorized.post(
        "/retrieval/search", json={"query": "forecast", "top_k": 1}
    )
    assert response.status_code == 200
    (hit,) = response.json()
    assert hit["document_id"] == best
    assert hit["score"] == pytest.approx(1.0)


def test_search_fails_closed_on_audit_error(authorized, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session

    upload(authorized)

    def fail_commit(session):
        raise SQLAlchemyError("private detail")

    monkeypatch.setattr(Session, "commit", fail_commit)
    response = authorized.post("/retrieval/search", json={"query": "phrase"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Search is unavailable."}


def test_incompatible_collection_is_preserved(vector_store):
    from qdrant_client import models

    vector_store.client.create_collection(
        vector_store.collection,
        vectors_config=models.VectorParams(size=4, distance=models.Distance.DOT),
    )
    with pytest.raises(ValueError, match="Incompatible"):
        vector_store.ensure_collection()
    params = vector_store.client.get_collection(
        vector_store.collection
    ).config.params.vectors
    assert params.size == 4
    assert params.distance == models.Distance.DOT
