"""Explicit maintenance command for a trusted database operator."""

import argparse

from app.db.session import get_session_factory
from app.services.document_acl import AclSyncPending, change_read_grant
from app.services.vector_store import get_vector_store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["grant", "revoke"])
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--document", required=True)
    parser.add_argument("--role", required=True)
    args = parser.parse_args()
    try:
        with get_session_factory()() as session:
            change_read_grant(
                session,
                get_vector_store(),
                tenant_id=args.tenant,
                document_id=args.document,
                role_id=args.role,
                grant=args.operation == "grant",
            )
    except AclSyncPending as exc:
        parser.exit(1, f"{exc}\n")
    except Exception:
        parser.exit(1, "Operation failed; check scope and service availability.\n")


if __name__ == "__main__":
    main()
