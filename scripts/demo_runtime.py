"""Explicit targets for the demo tools. Importing this module opens no connections."""

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import create_session_factory, get_db_session
from app.services.embeddings import Embeddings, get_embeddings, local_embeddings
from app.services.llm import LocalProvider, get_provider
from app.services.vector_store import VectorStore, get_vector_store


@dataclass
class Runtime:
    api: httpx.Client
    sessions: sessionmaker
    store: VectorStore
    encoder: Embeddings
    key: str = field(repr=False)


@contextmanager
def local_runtime():
    """A fresh SQLite database, private upload root and in-process vector store."""
    with TemporaryDirectory() as tmp:
        db_url = "sqlite:///" + str(Path(tmp) / "demo.sqlite")
        env = {
            "APP_ENV": "test",
            "DATABASE_URL": db_url,
            "JWT_SECRET_KEY": token_urlsafe(48),
            "JWT_ALGORITHM": "HS256",
            "UPLOAD_DIR": str(Path(tmp) / "uploads"),
            "LLM_PROVIDER": "local",
            "EMBEDDING_PROVIDER": "local",
            "CHUNK_SIZE": "160",
            "CHUNK_OVERLAP": "24",
            "EMBEDDING_DIMENSION": "384",
            "MAX_UPLOAD_BYTES": "5242880",
        }
        with patch.dict(os.environ, env):
            get_settings.cache_clear()
            from app.main import app

            engine = create_engine(db_url)

            @event.listens_for(engine, "connect")
            def enable_fk(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")

            sessions = create_session_factory(engine)
            client = QdrantClient(":memory:", force_disable_check_same_thread=True)
            store = VectorStore(client, "demo", 384)
            encoder = local_embeddings(384)

            def db():
                with sessions() as session:
                    yield session

            overrides = {
                get_db_session: db,
                get_vector_store: lambda: store,
                get_embeddings: lambda: encoder,
                get_provider: LocalProvider,
            }
            try:
                Base.metadata.create_all(engine)
                with (
                    patch.dict(app.dependency_overrides, overrides),
                    TestClient(app) as api,
                ):
                    yield Runtime(api, sessions, store, encoder, token_urlsafe(32))
            finally:
                client.close()
                engine.dispose()
                get_settings.cache_clear()


@contextmanager
def staging_runtime(base_url):
    """Require a dedicated pre-migrated staging target and serialize corpus tools."""
    settings = get_settings()
    url = urlsplit(base_url)
    if (
        url.username
        or url.password
        or url.query
        or url.fragment
        or not url.hostname
        or not (
            url.scheme == "https"
            or (
                url.scheme == "http"
                and url.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        )
    ):
        raise ValueError("Use HTTPS or a loopback staging API URL.")
    db_url = make_url(settings.database_url or "sqlite://")
    key = os.environ.get("DEMO_PASSWORD_KEY", "")
    if (
        settings.app_env != "staging"
        or db_url.get_backend_name() != "mysql"
        or db_url.database != "rag_demo"
        or settings.qdrant_collection_name != "rag_demo_chunks"
        or settings.llm_provider != "local"
        or settings.embedding_provider != "local"
        or len(key) < 32
    ):
        raise ValueError(
            "Staging demo targets, local providers and a demo key are required."
        )
    with httpx.Client(base_url=base_url, timeout=30, trust_env=False) as api:
        health = api.get("/health")
        health.raise_for_status()
        if health.json().get("environment") != "staging":
            raise ValueError("API did not identify itself as staging.")
        engine = create_engine(db_url, hide_parameters=True)
        try:
            with engine.connect() as lock:
                if lock.scalar(text("SELECT GET_LOCK('rag_demo_evaluation', 0)")) != 1:
                    raise RuntimeError("Another staging demo tool is running.")
                try:
                    yield Runtime(
                        api,
                        create_session_factory(engine),
                        get_vector_store(),
                        get_embeddings(),
                        key,
                    )
                finally:
                    lock.execute(text("SELECT RELEASE_LOCK('rag_demo_evaluation')"))
        finally:
            engine.dispose()


def runtime(staging_url):
    return staging_runtime(staging_url) if staging_url else local_runtime()
