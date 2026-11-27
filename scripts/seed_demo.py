"""Opt-in demo corpus. Run from the repository root with python -m scripts.seed_demo."""

import argparse
import hmac
import json
from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.core.config import get_settings
from app.core.passwords import hash_password
from app.db.models import Document, Role, Tenant, User, UserRole
from app.services.chunking import chunk_text
from app.services.document_acl import change_read_grant
from scripts.demo_runtime import Runtime, runtime

COMPANIES = {"acme": "Acme Corp", "globex": "Globex Inc"}
PERSONAS = ("employee", "hr", "admin")


def stable_id(company, kind):
    return str(
        uuid5(NAMESPACE_URL, f"urn:permission-aware-rag:demo:v1:{company}:{kind}")
    )


def role_id(company, persona):
    return stable_id(company, f"role/{persona}")


@dataclass(frozen=True)
class Source:
    company: str
    tier: str
    owner: str
    grants: tuple[str, ...]
    text: str
    snippets: tuple[str, ...]


def sources(company):
    analyst, lead, lead_salary = {
        "acme": ("Nell Vale", "Milo Park", "151,900"),
        "globex": ("Fern Reed", "Ari Dale", "167,300"),
    }[company]
    policy, desk, runbook, queue, band, salary = {
        "acme": (
            "Fieldstone",
            "Desk P17",
            "Amber Relay",
            "OPS-A41",
            "Aster Six",
            "118,450",
        ),
        "globex": (
            "Westgate",
            "Desk Q39",
            "Violet Relay",
            "OPS-G82",
            "Orchid Nine",
            "143,720",
        ),
    }[company]
    return (
        Source(
            company,
            "policy",
            "employee",
            ("hr", "admin"),
            f"The visitor policy requires check-in at {policy}. "
            f"Badges are held at {desk}.",
            (policy, desk),
        ),
        Source(
            company,
            "runbook",
            "admin",
            ("employee",),
            f"The recovery runbook uses {runbook}. Route incident tickets to {queue}.",
            (runbook, queue),
        ),
        Source(
            company,
            "salary",
            "admin",
            ("hr",),
            f"The salary band for {band} is USD {salary}. "
            f"Payroll worksheet for the upcoming compensation review. "
            f"Annual base pay excludes retirement contributions and benefits.\n"
            f"Analyst: {analyst} | Annual base: USD {salary}\n"
            f"Team lead: {lead} | Annual base: USD {lead_salary}",
            (band, salary, analyst, lead, lead_salary),
        ),
    )


@dataclass(frozen=True)
class Entry:
    source: Source
    document_id: str
    chunk_ids: tuple[str, ...]


@dataclass
class Corpus:
    entries: list[Entry]
    tokens: dict[tuple[str, str], str] = field(repr=False)

    def headers(self, company, persona):
        return {"Authorization": "Bearer " + self.tokens[company, persona]}


def credentials(rt, company, persona):
    email = f"{persona}@{company}.example.test"
    password = hmac.digest(rt.key.encode(), email.encode(), "sha256").hex()
    return {
        "tenant_id": stable_id(company, "tenant"),
        "email": email,
        "password": password,
    }


def identities(rt, company):
    tid = stable_id(company, "tenant")
    with rt.sessions.begin() as session:
        if not session.get(Tenant, tid):
            session.add(Tenant(id=tid, name=COMPANIES[company]))
            session.flush()
        for persona in PERSONAS:
            creds = credentials(rt, company, persona)
            rid, uid = role_id(company, persona), stable_id(company, f"user/{persona}")
            if not session.get(Role, rid):
                session.add(Role(id=rid, tenant_id=tid, name=persona))
            if not session.get(User, uid):
                session.add(
                    User(
                        id=uid,
                        tenant_id=tid,
                        email=creds["email"],
                        password_hash=hash_password(creds["password"]),
                    )
                )
            session.flush()
            if not session.get(UserRole, (tid, uid, rid)):
                session.add(UserRole(tenant_id=tid, user_id=uid, role_id=rid))


def set_grant(rt, entry, persona, grant):
    company = entry.source.company
    with rt.sessions() as session:
        change_read_grant(
            session,
            rt.store,
            tenant_id=stable_id(company, "tenant"),
            document_id=entry.document_id,
            role_id=role_id(company, persona),
            grant=grant,
        )


def prepare(rt: Runtime, *, seed: bool) -> Corpus:
    """Login and inventory persisted IDs. A benchmark can load without reseeding."""
    corpus = Corpus([], {})
    with rt.sessions() as session:
        allowed = {stable_id(c, "tenant"): name for c, name in COMPANIES.items()}
        if any(allowed.get(t.id) != t.name for t in session.scalars(select(Tenant))):
            raise ValueError("Use a database dedicated to this demo corpus.")
    for company in COMPANIES:
        if seed:
            identities(rt, company)
        for persona in PERSONAS:
            login = rt.api.post("/auth/login", json=credentials(rt, company, persona))
            login.raise_for_status()
            corpus.tokens[company, persona] = login.json()["access_token"]

        for source in sources(company):
            filename = f"demo-v1-{company}-{source.tier}.txt"
            stmt = select(Document).where(
                Document.tenant_id == stable_id(company, "tenant"),
                Document.name == filename,
            )
            with rt.sessions() as session:
                doc_id = session.scalars(
                    stmt.with_only_columns(Document.id)
                ).one_or_none()
            if doc_id is None and seed:
                response = rt.api.post(
                    "/documents/upload",
                    content=source.text.encode(),
                    headers={
                        **corpus.headers(company, source.owner),
                        "X-Filename": filename,
                        "Content-Type": "text/plain",
                    },
                )
                response.raise_for_status()
                doc_id = response.json()["document_id"]
            with rt.sessions() as session:
                doc = session.scalars(stmt).one()
                chunks = sorted(doc.chunks, key=lambda c: c.chunk_index)
                settings = get_settings()
                expected = chunk_text(
                    source.text, settings.chunk_size, settings.chunk_overlap
                )
                if (
                    doc.id != doc_id
                    or doc.owner_user_id != stable_id(company, f"user/{source.owner}")
                    or [c.chunk_text for c in chunks] != expected
                ):
                    raise ValueError(
                        "Existing corpus document differs from its specification."
                    )
                entry = Entry(source, doc.id, tuple(c.id for c in chunks))
            if seed:
                for persona in source.grants:
                    set_grant(rt, entry, persona, True)
            corpus.entries.append(entry)
    return corpus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-url", help="Explicit dedicated staging API URL")
    parser.add_argument(
        "--show-logins", action="store_true",
        help="Print the six staging login payloads and their current bearer tokens.",
    )
    args = parser.parse_args()
    if args.show_logins and not args.staging_url:
        parser.error("--show-logins requires --staging-url")
    with runtime(args.staging_url) as rt:
        corpus = prepare(rt, seed=True)
        print(
            f"Verified {len(COMPANIES)} tenants, {len(corpus.tokens)} personas, "
            f"{len(corpus.entries)} documents."
        )
        if not args.staging_url:
            print("Local demo uses disposable storage.")
        if args.show_logins:
            for (company, persona), token in corpus.tokens.items():
                print(f"{company}/{persona}")
                print(json.dumps(credentials(rt, company, persona)))
                print(f"Bearer {token}")


if __name__ == "__main__":
    main()
