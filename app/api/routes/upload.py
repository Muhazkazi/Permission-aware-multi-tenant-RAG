"""Authenticated raw-body upload. Bound the stream before processing it."""

import asyncio
from pathlib import PureWindowsPath
from threading import BoundedSemaphore
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_tenant_principal
from app.core.config import Settings, get_settings
from app.core.principal import AuthenticatedPrincipal
from app.core.upload_events import correlation_id, upload_event
from app.db.session import get_db_session
from app.schemas.documents import DocumentResponse
from app.services.embeddings import Embeddings, get_embeddings
from app.services.extraction import InvalidDocument
from app.services.upload import process_upload
from app.services.vector_store import VectorStore, get_vector_store


class UploadRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()

        async def traced(request: Request):
            identifier = uuid4().hex
            token = correlation_id.set(identifier)
            try:
                response = await handle(request)
                response.headers["X-Correlation-ID"] = identifier
                upload_event("request", "completed", response.status_code)
                return response
            except HTTPException as error:
                error.headers = {
                    **(error.headers or {}),
                    "X-Correlation-ID": identifier,
                }
                upload_event("request", "rejected", error.status_code)
                raise
            except RequestValidationError:
                upload_event("validation", "rejected", 422)
                raise
            except Exception:
                upload_event("request", "failed")
                raise HTTPException(
                    500,
                    "Could not store the document.",
                    headers={"X-Correlation-ID": identifier},
                ) from None
            finally:
                correlation_id.reset(token)

        return traced


router = APIRouter(prefix="/documents", tags=["documents"], route_class=UploadRoute)
_MEDIA = {".txt": "text/plain", ".pdf": "application/pdf"}
_slots = BoundedSemaphore(2)


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                mime: {"schema": {"type": "string", "format": "binary"}}
                for mime in _MEDIA.values()
            },
        }
    },
)
async def upload_document(
    request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_tenant_principal)],
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[VectorStore, Depends(get_vector_store)],
    encoder: Annotated[Embeddings, Depends(get_embeddings)],
    x_filename: Annotated[str, Header(min_length=1, max_length=255)],
) -> DocumentResponse:
    filename = PureWindowsPath(x_filename)
    if (
        filename.name != x_filename
        or ":" in x_filename
        or not x_filename.isprintable()
        or x_filename != x_filename.strip()
    ):
        raise HTTPException(422, "Invalid filename.")
    media = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media != _MEDIA.get(filename.suffix.lower()):
        raise HTTPException(415, "Expected a matching PDF or TXT file type.")
    if not _slots.acquire(blocking=False):
        raise HTTPException(
            503, "Upload capacity reached.", headers={"Retry-After": "1"}
        )
    try:
        upload_event("admission", "accepted")
        data = bytearray()
        try:
            async with asyncio.timeout(60):
                async for part in request.stream():
                    if len(data) + len(part) > settings.max_upload_bytes:
                        raise HTTPException(413, "Upload exceeds the size limit.")
                    data.extend(part)
        except TimeoutError:
            raise HTTPException(408, "Upload timed out.") from None
        try:
            return await run_in_threadpool(
                process_upload,
                session,
                principal,
                settings,
                x_filename,
                media,
                bytes(data),
                store,
                encoder,
            )
        except InvalidDocument:
            raise HTTPException(
                422, "File cannot be processed as meaningful text."
            ) from None
        except Exception:
            raise HTTPException(500, "Could not store the document.") from None
    finally:
        _slots.release()
