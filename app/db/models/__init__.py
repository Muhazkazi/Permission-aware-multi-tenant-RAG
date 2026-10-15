"""Central import path for all ORM models.

Import this module before inspecting ``Base.metadata`` so Alembic can discover
every mapped table.
"""

from app.db.models.audit_log import RetrievalAuditLog
from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk
from app.db.models.document_permission import DocumentPermission
from app.db.models.role import Role
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.db.models.user_role import UserRole

__all__ = [
    "Document",
    "DocumentChunk",
    "DocumentPermission",
    "RetrievalAuditLog",
    "Role",
    "Tenant",
    "User",
    "UserRole",
]
