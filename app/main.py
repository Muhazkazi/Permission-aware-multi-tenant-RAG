"""FastAPI application entry point."""

from fastapi import FastAPI

from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name, version=settings.app_version)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Return a lightweight signal that the API is available."""

    return {"status": "ok", "environment": settings.app_env}
