"""HTTP integration tests with real JWTs and a private database per test."""

from secrets import token_urlsafe

import jwt
import pytest

from app.core.config import get_settings
from app.core.passwords import hash_password
from app.db.models import Document, DocumentPermission, Role, Tenant, User, UserRole


@pytest.fixture
def shared_document(sessions, credentials) -> str:
    """Give a non-owner access exclusively through a reader membership and grant."""
    with sessions.begin() as session:
        tenant = session.get_one(Tenant, credentials["tenant_id"])
        (reader,) = tenant.users
        owner = User(
            tenant=tenant,
            email="owner@example.test",
            password_hash=hash_password(token_urlsafe(24)),
        )
        document = Document(tenant=tenant, owner=owner, name="Shared", storage_key="s")
        role = Role(tenant=tenant, name="reader")
        session.add_all(
            [
                UserRole(user=reader, role=role),
                DocumentPermission(document=document, role=role),
            ]
        )
        session.flush()
        assert document.owner_user_id != reader.id
        return document.id


def test_signed_login_reads_document_using_database_role(
    client, shared_document, reader_token
):
    settings = get_settings()
    claims = jwt.decode(
        reader_token, settings.require_jwt_secret(), algorithms=[settings.jwt_algorithm]
    )
    assert "roles" not in claims
    client.headers["Authorization"] = f"Bearer {reader_token}"
    identity = client.get("/auth/me")
    assert identity.status_code == 200
    assert identity.json() == {
        "user_id": claims["sub"],
        "tenant_id": claims["tenant_id"],
        "roles": ["reader"],
    }
    response = client.get(f"/documents/{shared_document}")
    assert response.status_code == 200
    assert response.json()["document_id"] == shared_document


@pytest.mark.parametrize("revoke", ["membership", "permission"])
def test_revocation_on_next_request(
    client, sessions, shared_document, reader_token, revoke
):
    client.headers["Authorization"] = f"Bearer {reader_token}"
    path = f"/documents/{shared_document}"
    assert client.get(path).status_code == 200
    with sessions.begin() as session:
        document = session.get_one(Document, shared_document)
        (grant,) = document.permissions
        if revoke == "membership":
            (membership,) = grant.role.user_roles
            session.delete(membership)
        else:
            session.delete(grant)

    denied = client.get(path)
    assert denied.status_code == 404
    assert denied.json() == {"detail": "Document not found."}
    identity = client.get("/auth/me")
    assert identity.status_code == 200
    assert identity.json()["roles"] == ([] if revoke == "membership" else ["reader"])


def test_other_tenants_document_is_indistinguishable_from_missing(
    client, sessions, credentials, reader_token
):
    with sessions.begin() as session:
        first = session.get_one(Tenant, credentials["tenant_id"])
        (owner,) = first.users
        second = Tenant(name="Second")
        other_owner = User(
            tenant=second,
            email=owner.email,
            password_hash=hash_password(token_urlsafe(24)),
        )
        own = Document(tenant=first, owner=owner, storage_key="a", name="Own")
        other = Document(
            tenant=second, owner=other_owner, storage_key="b", name="Other"
        )
        session.add_all([own, other])

    client.headers["Authorization"] = f"Bearer {reader_token}"
    assert client.get(f"/documents/{own.id}").status_code == 200
    denied = client.get(f"/documents/{other.id}")
    missing = client.get("/documents/missing")
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json() == {"detail": "Document not found."}


def test_disabling_user_rejects_existing_token(client, sessions, reader_token):
    client.headers["Authorization"] = f"Bearer {reader_token}"
    identity = client.get("/auth/me")
    assert identity.status_code == 200
    with sessions.begin() as session:
        session.get_one(User, identity.json()["user_id"]).is_active = False

    denied = client.get("/auth/me")
    assert denied.status_code == 401
    assert denied.json() == {"detail": "Could not validate credentials."}
    assert denied.headers["WWW-Authenticate"] == "Bearer"
