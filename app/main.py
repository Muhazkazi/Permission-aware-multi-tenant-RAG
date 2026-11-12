"""FastAPI application entry point."""

from fastapi import FastAPI

from app.api.routes.auth import router as auth_router
from app.api.routes.documents import router as documents_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.include_router(auth_router)
app.include_router(documents_router)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Return a lightweight signal that the API is available."""

    return {"status": "ok", "environment": settings.app_env}
