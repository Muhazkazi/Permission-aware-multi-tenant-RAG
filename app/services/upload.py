"""Persist one validated upload and its chunks in the principal's tenant."""

import os
from contextlib import ExitStack
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.principal import AuthenticatedPrincipal
from app.core.upload_events import upload_event
from app.db.models import Document, DocumentChunk
from app.schemas.documents import DocumentResponse
from app.services.chunking import chunk_text
from app.services.document_service import create_document_metadata
from app.services.extraction import InvalidDocument, extract_text
from app.services.private_storage import tenant_directory


def process_upload(
    session: Session,
    principal: AuthenticatedPrincipal,
    settings: Settings,
    name: str,
    media_type: str,
    data: bytes,
) -> DocumentResponse:
    try:
        text = extract_text(data, media_type)
    except Exception:
        upload_event("extraction", "failed")
        raise
    try:
        chunks = chunk_text(text, settings.chunk_size, settings.chunk_overlap)
    except ValueError:
        upload_event("chunking", "rejected")
        raise InvalidDocument from None

    tenant_id = principal.tenant_id
    namespace = sha256(tenant_id.encode()).hexdigest()
    leaf = uuid4().hex
    key = f"{namespace}/{leaf}"
    path = settings.upload_dir / key
    created_file = False
    committing = False
    handles = ExitStack()
    stage = "persistence"
    try:
        document = create_document_metadata(
            session, principal, name=name, content_type=media_type, storage_key=key
        )
        for index, chunk in enumerate(chunks):
            session.add(
                DocumentChunk(
                    tenant_id=tenant_id,
                    document_id=document.id,
                    chunk_index=index,
                    chunk_text=chunk,
                    content_hash=sha256(chunk.encode()).hexdigest(),
                    token_count=len(chunk.split()),
                    qdrant_point_id=str(uuid4()),
                )
            )
        session.flush()
        response = DocumentResponse.from_document(document)

        stage = "storage"
        directory = handles.enter_context(
            tenant_directory(settings.upload_dir, namespace)
        )
        filename = path if directory is None else leaf
        fd = os.open(
            filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
            dir_fd=directory,
        )
        created_file = True
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        stage = "commit"
        committing = True
        session.commit()
        return response
    except Exception:
        upload_event(stage, "failed")
        try:
            session.rollback()
        except Exception:
            upload_event("rollback", "failed")
            raise
        if created_file:
            # A lost acknowledgement can mean COMMIT actually succeeded. On
            # uncertainty keep the file. A locking read waits for the insert
            # to resolve on the same primary before deciding it is absent.
            try:
                persisted = (
                    committing
                    and session.scalar(
                        select(Document.id)
                        .where(
                            Document.tenant_id == tenant_id, Document.storage_key == key
                        )
                        .with_for_update()
                    )
                    is not None
                )
            except Exception:
                upload_event("reconciliation", "failed")
                raise
            if not persisted:
                try:
                    os.unlink(filename, dir_fd=directory)
                except FileNotFoundError:
                    pass
                except OSError:
                    upload_event("cleanup", "failed")
                    raise
        raise
    finally:
        handles.close()
