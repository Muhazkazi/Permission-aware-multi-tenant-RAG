"""Offline tests for ORM metadata and tenant-isolation constraints."""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.orm import configure_mappers

import app.db.models  # noqa: F401
from app.db.base import Base


def _constraint_names(
    table_name: str, constraint_type: type[object]
) -> set[str | None]:
    """Return names for one kind of table constraint."""

    table = Base.metadata.tables[table_name]
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, constraint_type)
    }


def _index_names(table_name: str) -> set[str | None]:
    """Return explicit index names for a mapped table."""

    return {index.name for index in Base.metadata.tables[table_name].indexes}


def test_all_application_models_are_registered_with_metadata() -> None:
    """The model package must register every application table for Alembic."""

    configure_mappers()

    assert set(Base.metadata.tables) == {
        "document_chunks",
        "document_permissions",
        "documents",
        "retrieval_audit_logs",
        "roles",
        "tenants",
        "user_roles",
        "users",
    }


def test_tenant_scoped_uniqueness_and_cross_tenant_foreign_keys_exist() -> None:
    """Database constraints must protect tenant boundaries without API logic."""

    assert {
        "uq_users_tenant_id_email",
        "uq_users_tenant_id_id",
    } <= _constraint_names("users", UniqueConstraint)
    assert {
        "uq_roles_tenant_id_name",
        "uq_roles_tenant_id_id",
    } <= _constraint_names("roles", UniqueConstraint)
    assert {
        "uq_documents_tenant_id_id",
        "uq_documents_tenant_storage_key",
    } <= _constraint_names("documents", UniqueConstraint)
    assert "uq_document_chunks_tenant_document_index" in _constraint_names(
        "document_chunks", UniqueConstraint
    )

    assert {
        "fk_user_roles_tenant_user",
        "fk_user_roles_tenant_role",
    } <= _constraint_names("user_roles", ForeignKeyConstraint)
    assert "fk_documents_tenant_owner" in _constraint_names(
        "documents", ForeignKeyConstraint
    )
    assert "fk_document_permissions_tenant_document" in _constraint_names(
        "document_permissions", ForeignKeyConstraint
    )
    assert "fk_document_permissions_tenant_role" in _constraint_names(
        "document_permissions", ForeignKeyConstraint
    )
    assert "fk_document_chunks_tenant_document" in _constraint_names(
        "document_chunks", ForeignKeyConstraint
    )
    assert "fk_retrieval_audit_logs_tenant_user" in _constraint_names(
        "retrieval_audit_logs", ForeignKeyConstraint
    )


def test_indexes_and_security_columns_are_present() -> None:
    """Likely tenant lookups and audit fields should be indexed and available."""

    assert "ix_documents_tenant_owner_user_id" in _index_names("documents")
    assert "ix_document_permissions_tenant_role_id" in _index_names(
        "document_permissions"
    )
    assert "ix_document_chunks_tenant_document_id" in _index_names("document_chunks")
    assert "ix_retrieval_audit_logs_tenant_created_at" in _index_names(
        "retrieval_audit_logs"
    )

    user_columns = Base.metadata.tables["users"].columns
    chunk_columns = Base.metadata.tables["document_chunks"].columns
    audit_columns = Base.metadata.tables["retrieval_audit_logs"].columns

    for table_name in {
        "document_chunks",
        "document_permissions",
        "documents",
        "retrieval_audit_logs",
        "roles",
        "user_roles",
        "users",
    }:
        assert "tenant_id" in Base.metadata.tables[table_name].columns

    assert "password_hash" in user_columns
    assert "password" not in user_columns
    assert "embedding" not in chunk_columns
    assert {"query_hash", "outcome", "retrieved_chunk_ids"} <= set(audit_columns.keys())
    assert "ck_retrieval_audit_logs_outcome" in _constraint_names(
        "retrieval_audit_logs", CheckConstraint
    )
