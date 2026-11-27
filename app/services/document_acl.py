"""Trusted operator service for role read grants, using a dedicated session."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentPermission, Role
from app.services.vector_store import VectorStore


class AclSyncPending(RuntimeError):
    """SQL is committed; repeat the same operation to retry the payload sync."""


def change_read_grant(
    session: Session,
    store: VectorStore,
    *,
    tenant_id: str,
    document_id: str,
    role_id: str,
    grant: bool,
) -> None:
    locked = (
        select(Document)
        .where(Document.tenant_id == tenant_id, Document.id == document_id)
        .with_for_update()
    )
    with session.begin():
        session.scalars(locked).one()
        session.scalars(
            select(Role.id).where(Role.id == role_id, Role.tenant_id == tenant_id)
        ).one()
        permission = session.scalar(
            select(DocumentPermission).where(
                DocumentPermission.document_id == document_id,
                DocumentPermission.tenant_id == tenant_id,
                DocumentPermission.role_id == role_id,
                DocumentPermission.permission == "read",
            )
        )
        if grant and permission is None:
            session.add(
                DocumentPermission(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    role_id=role_id,
                    permission="read",
                )
            )
        elif not grant and permission is not None:
            session.delete(permission)

    # Serialize sync with other edits and reload after commit, including retries.
    try:
        with session.begin():
            doc = session.scalars(
                locked.execution_options(populate_existing=True)
            ).one()
            session.expire(doc, ["permissions"])
            store.sync_document_acl(doc)
    except Exception:
        raise AclSyncPending(
            "Grant state committed; vector sync needs retry."
        ) from None
