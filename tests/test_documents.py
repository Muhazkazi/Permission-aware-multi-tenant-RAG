"""Isolated tests for tenant-scoped document metadata authorization."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401
from app.api.deps import get_current_tenant_principal
from app.api.routes import documents as document_routes
from app.core.principal import AuthenticatedPrincipal
from app.db.base import Base
from app.db.models import Document, DocumentPermission, Role, Tenant, User
from app.db.session import get_db_session
from app.main import app
from app.schemas.documents import DocumentResponse
from app.services.document_service import (
    DocumentNotFoundError,
    create_document_metadata,
    get_accessible_document,
    list_accessible_documents,
)


@pytest.fixture
def document_session() -> Iterator[Session]:
    """Create a temporary in-memory database for document authorization tests."""

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture(autouse=True)
def clear_document_dependency_overrides() -> Iterator[None]:
    """Ensure document route overrides do not affect other test modules."""

    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _principal(
    user: User, tenant: Tenant, roles: tuple[Role, ...] = ()
) -> AuthenticatedPrincipal:
    """Build an authenticated principal from isolated database objects."""

    return AuthenticatedPrincipal(user=user, tenant=tenant, roles=roles)


def _add_document(
    session: Session, *, tenant_id: str, owner_user_id: str, document_id: str, name: str
) -> Document:
    """Add one isolated document record with an opaque internal storage key."""

    document = Document(
        id=document_id,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        name=name,
        storage_key=f"pending/{document_id}",
    )
    session.add(document)
    return document


def _setup_tenant_data(
    session: Session,
) -> tuple[Tenant, User, User, Role, Document, Document]:
    """Create isolated tenant records used to exercise owner and role access."""

    tenant = Tenant(id="tenant-1", name="Tenant One")
    other_tenant = Tenant(id="tenant-2", name="Tenant Two")
    owner = User(
        id="user-owner",
        tenant_id=tenant.id,
        email="owner@example.test",
        password_hash="hash",
        is_active=True,
    )
    reader = User(
        id="user-reader",
        tenant_id=tenant.id,
        email="reader@example.test",
        password_hash="hash",
        is_active=True,
    )
    role = Role(id="role-reader", tenant_id=tenant.id, name="reader")
    owned_document = _add_document(
        session,
        tenant_id=tenant.id,
        owner_user_id=owner.id,
        document_id="document-owned",
        name="Owned document",
    )
    granted_document = _add_document(
        session,
        tenant_id=tenant.id,
        owner_user_id=owner.id,
        document_id="document-granted",
        name="Granted document",
    )
    other_document = _add_document(
        session,
        tenant_id=other_tenant.id,
        owner_user_id="user-other",
        document_id="document-other-tenant",
        name="Other tenant document",
    )
    other_user = User(
        id="user-other",
        tenant_id=other_tenant.id,
        email="other@example.test",
        password_hash="hash",
        is_active=True,
    )
    session.add_all([tenant, other_tenant, owner, reader, role, other_user])
    session.add(
        DocumentPermission(
            id="permission-reader",
            tenant_id=tenant.id,
            document_id=granted_document.id,
            role_id=role.id,
            permission="read",
        )
    )
    session.commit()
    assert other_document.id == "document-other-tenant"
    return tenant, owner, reader, role, owned_document, granted_document


def test_document_creation_uses_authenticated_tenant_and_owner(
    document_session: Session,
) -> None:
    """Creation derives tenant and owner from the principal."""

    tenant = Tenant(id="tenant-1", name="Tenant One")
    user = User(
        id="user-1",
        tenant_id=tenant.id,
        email="user@example.test",
        password_hash="hash",
        is_active=True,
    )
    document_session.add_all([tenant, user])
    document_session.commit()

    document = create_document_metadata(
        document_session,
        _principal(user, tenant),
        name="Architecture notes",
        content_type="text/markdown",
    )

    assert document.tenant_id == tenant.id
    assert document.owner_user_id == user.id
    assert document.storage_key.startswith("pending/")
    assert document_session.scalar(select(Document).where(Document.id == document.id))


def test_listing_and_reading_include_only_owned_or_role_granted_documents(
    document_session: Session,
) -> None:
    """Role grants expose same-tenant documents while unrelated records stay hidden."""

    tenant, owner, reader, role, owned_document, granted_document = _setup_tenant_data(
        document_session
    )

    owner_documents = list_accessible_documents(
        document_session, _principal(owner, tenant)
    )
    reader_documents = list_accessible_documents(
        document_session, _principal(reader, tenant, (role,))
    )

    assert {document.id for document in owner_documents} == {
        owned_document.id,
        granted_document.id,
    }
    assert {document.id for document in reader_documents} == {granted_document.id}
    assert (
        get_accessible_document(
            document_session, _principal(reader, tenant, (role,)), granted_document.id
        ).id
        == granted_document.id
    )


def test_cross_tenant_and_ungranted_document_reads_are_not_found(
    document_session: Session,
) -> None:
    """Missing authorization and another tenant's ID both produce not-found results."""

    tenant, _owner, reader, _role, owned_document, _granted_document = (
        _setup_tenant_data(document_session)
    )

    with pytest.raises(DocumentNotFoundError):
        get_accessible_document(
            document_session, _principal(reader, tenant), owned_document.id
        )
    with pytest.raises(DocumentNotFoundError):
        get_accessible_document(
            document_session, _principal(reader, tenant), "document-other-tenant"
        )


def test_document_routes_return_safe_metadata_and_validate_input(
    document_session: Session,
) -> None:
    """Routes omit internal storage keys and reject malformed creation payloads."""

    tenant = Tenant(id="tenant-1", name="Tenant One")
    user = User(
        id="user-1",
        tenant_id=tenant.id,
        email="user@example.test",
        password_hash="hash",
        is_active=True,
    )
    document_session.add_all([tenant, user])
    document_session.commit()
    principal = _principal(user, tenant)
    app.dependency_overrides[get_current_tenant_principal] = lambda: principal
    app.dependency_overrides[get_db_session] = lambda: document_session
    client = TestClient(app)

    invalid_response = client.post("/documents", json={"name": ""})
    create_response = client.post(
        "/documents", json={"name": "Product brief", "content_type": "text/plain"}
    )
    list_response = client.get("/documents")
    other_user = User(
        id="user-2",
        tenant_id=tenant.id,
        email="other@example.test",
        password_hash="hash",
        is_active=True,
    )
    document_session.add(other_user)
    document_session.commit()
    other_principal = _principal(other_user, tenant)
    app.dependency_overrides[get_current_tenant_principal] = lambda: other_principal
    denied_response = client.get(f"/documents/{create_response.json()['document_id']}")

    assert invalid_response.status_code == 422
    assert create_response.status_code == 201
    assert set(create_response.json()) == {
        "document_id",
        "name",
        "content_type",
        "created_at",
        "updated_at",
    }
    assert "storage_key" not in create_response.json()
    assert list_response.status_code == 200
    assert [document["name"] for document in list_response.json()] == ["Product brief"]
    assert denied_response.status_code == 404
    assert denied_response.json() == {"detail": "Document not found."}


@pytest.fixture
def creator(document_session):
    tenant = Tenant(id="tenant-1", name="Tenant One")
    user = User(
        id="user-1",
        tenant_id=tenant.id,
        email="user@example.test",
        password_hash="unused",
        is_active=True,
    )
    document_session.add_all([tenant, user])
    document_session.commit()
    principal = _principal(user, tenant)
    app.dependency_overrides[get_current_tenant_principal] = lambda: principal
    app.dependency_overrides[get_db_session] = lambda: document_session
    return principal


def test_repeated_missing_document_raises_fresh_404(document_session, creator):
    errors = []
    for _ in range(2):
        with pytest.raises(HTTPException) as caught:
            document_routes.get_document("missing", creator, document_session)
        assert caught.value.status_code == 404
        assert caught.value.detail == "Document not found."
        errors.append(caught.value)
    assert errors[0] is not errors[1]


@pytest.mark.parametrize(
    ("stage", "status"),
    [("flush", 409), ("refresh", 500), ("response", 500), ("commit", 500)],
)
def test_failed_creation_rolls_back(
    document_session, creator, monkeypatch, stage, status
):
    def fail(*_args, **_kwargs):
        if stage == "flush":
            raise IntegrityError("internal detail", None, ValueError())
        if stage == "response":
            return DocumentResponse.model_validate({})
        raise SQLAlchemyError("internal detail")

    with monkeypatch.context() as patch:
        if stage == "response":
            patch.setattr(document_routes, "_document_response", fail)
        else:
            patch.setattr(document_session, stage, fail)
        response = TestClient(app).post("/documents", json={"name": "Rollback test"})
    assert response.status_code == status
    assert "internal detail" not in response.text
    assert not document_session.in_transaction()
    assert document_session.scalar(select(Document.id)) is None


def test_creation_returns_prepared_response_without_post_commit_sql(
    document_session, creator
):
    document_session.expire_on_commit = True
    committed = False

    @event.listens_for(document_session, "after_commit")
    def mark_commit(_session):
        nonlocal committed
        committed = True

    @event.listens_for(document_session.get_bind(), "before_cursor_execute")
    def check_sql(*_args):
        assert not committed, "Unexpected SQL after commit"

    try:
        response = TestClient(app).post("/documents", json={"name": "Prepared"})
        assert committed
        assert response.status_code == 201
        assert response.json()["created_at"]
        assert response.json()["name"] == "Prepared"
    finally:
        event.remove(document_session.get_bind(), "before_cursor_execute", check_sql)
        event.remove(document_session, "after_commit", mark_commit)
