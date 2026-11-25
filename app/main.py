"""FastAPI application entry point."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes.auth import router as auth_router
from app.api.routes.documents import router as documents_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.include_router(auth_router)
app.include_router(documents_router)


# Only static labels and messages may enter validation responses. Pydantic
# errors can include the entire request body, even for a missing field.
_PUBLIC_FIELDS = {
    "tenant_id",
    "email",
    "password",
    "name",
    "content_type",
    "document_id",
}
_VALIDATION_MESSAGES = {
    "missing": "Field required.",
    "json_invalid": "Invalid JSON.",
    "string_type": "Expected a string.",
    "string_too_short": "String is too short.",
    "string_too_long": "String is too long.",
    "too_short": "Value is too short.",
    "too_long": "Value is too long.",
}


@app.exception_handler(RequestValidationError)
async def validation_error(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = []
    for error in exc.errors():
        location = error.get("loc", ())
        source = (
            location[0] if location and location[0] in {"body", "path"} else "request"
        )
        loc = [source]
        if len(location) > 1 and location[1] in _PUBLIC_FIELDS:
            loc.append(location[1])
        kind = error["type"]
        if kind not in _VALIDATION_MESSAGES:
            kind = "invalid"
        details.append(
            {
                "loc": loc,
                "type": kind,
                "msg": _VALIDATION_MESSAGES.get(kind, "Invalid value."),
            }
        )
    return JSONResponse(status_code=422, content={"detail": details})


@app.get("/health")
def health_check() -> dict[str, str]:
    """Return a lightweight signal that the API is available."""

    return {"status": "ok", "environment": settings.app_env}
