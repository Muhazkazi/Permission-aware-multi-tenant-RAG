# Permission-Aware Multi-Tenant RAG

This project will become a secure document-assistant API for multiple tenants.
Phase 4 adds password hashing and database-backed JWT authorization foundations.
It does not yet include login endpoints, document processing, vector search, or
LLM integration.

## Prerequisites

- Python 3.11 or newer
- Docker Desktop (optional, for containers)

## Local setup (PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item .env.example .env
# Edit .env and replace the MySQL password placeholders and JWT secret.
# Generate a secret, for example: python -c "import secrets; print(secrets.token_urlsafe(32))"
uvicorn app.main:app --reload
```

The API is available at `http://127.0.0.1:8000`.

## Health check

In another PowerShell window, run:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok","environment":"development"}
```

Interactive API documentation is available at `http://127.0.0.1:8000/docs`.

## Docker

```powershell
Copy-Item .env.example .env
# Edit .env and replace the MySQL password placeholders and JWT secret.
docker compose up --build
```

Apply the empty baseline migration after MySQL is healthy:

```powershell
docker compose exec api alembic upgrade head
```

## Tests and linting

```powershell
pytest
ruff check .
```
