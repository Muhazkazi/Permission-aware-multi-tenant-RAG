# Permission-Aware Multi-Tenant RAG

This project will become a secure document-assistant API for multiple tenants.
Phase 6 adds tenant-scoped document metadata endpoints. It does not yet include
file upload, document processing, vector search, or LLM integration.

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

## Authentication endpoints

`POST /auth/login` accepts `tenant_id`, `email`, and `password`, then returns a
short-lived bearer token for an existing active user. `GET /auth/me` requires
that bearer token and returns the authenticated user ID, tenant ID, and current
database-resolved roles.

Validation errors return field locations and static messages without submitted
values. Login performs an Argon2 check for unknown and disabled accounts. Timing
can still vary, and the API currently has no login rate limiting.

## Document metadata endpoints

`POST /documents` creates a metadata record owned by the authenticated user.
`GET /documents` and `GET /documents/{document_id}` return only documents the
authenticated user owns or can read through an existing role-based permission.
Responses never expose storage keys or file contents. Creation prepares response
fields before commit. A lost commit acknowledgement or HTTP response can still
leave the caller uncertain whether creation succeeded. Repeating a POST can
create another metadata record.

The current schema supports role-based document read grants, but it does not
define which role may administer permissions. This phase therefore intentionally
does not expose a permission grant or revoke endpoint rather than assuming that
an arbitrary role, including document ownership, can grant access.

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
