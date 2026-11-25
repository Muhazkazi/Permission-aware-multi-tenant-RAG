"""HTTP uploads use temporary storage, real JWTs and private SQLite databases."""

import json
import os
from io import BytesIO
from secrets import token_urlsafe

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.tokens import create_access_token
from app.db.models import Document, DocumentChunk, Tenant, User
from app.services import upload
from app.services.chunking import chunk_text
from app.services.private_storage import tenant_directory


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / "private"
    monkeypatch.setenv("UPLOAD_DIR", str(root))
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "4096")
    monkeypatch.setenv("CHUNK_SIZE", "8")
    monkeypatch.setenv("CHUNK_OVERLAP", "2")
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.fixture
def uploader(client, reader_token, storage):
    client.headers["Authorization"] = f"Bearer {reader_token}"

    def send(name, mime, data, **kwargs):
        return client.post(
            "/documents/upload",
            headers={"X-Filename": name, "Content-Type": mime},
            content=data,
            **kwargs,
        )

    return send


def pdf_bytes(text=False, encrypted=False):
    writer = PdfWriter()
    page = writer.add_blank_page(200, 200)
    if text:
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 10 50 Td (PDF words) Tj ET")
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt(token_urlsafe(20))
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize(
    ("text", "size", "overlap", "expected"),
    [
        ("", 4, 1, []),
        ("    ", 2, 0, []),
        ("ab", 4, 1, ["ab"]),
        ("abcd", 4, 3, ["abcd"]),
        ("abcdef", 3, 0, ["abc", "def"]),
        ("abcdefg", 4, 2, ["abcd", "cdef", "efg"]),
        ("界abc", 2, 1, ["界a", "ab", "bc"]),
        ("a   b", 2, 0, ["a ", "b"]),
    ],
)
def test_chunk_boundaries(text, size, overlap, expected):
    assert chunk_text(text, size, overlap) == expected


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (4, 4), (4, -1)])
def test_invalid_chunk_configuration(size, overlap):
    with pytest.raises(ValueError):
        chunk_text("abc", size, overlap)


@pytest.mark.parametrize("is_pdf", [False, True], ids=["txt", "pdf"])
def test_upload_round_trip(uploader, sessions, client, storage, is_pdf):
    name, mime, data, text = (
        ("paper.pdf", "application/pdf", pdf_bytes(text=True), "PDF words")
        if is_pdf
        else ("paper.txt", "text/plain", b"original text", "original text")
    )
    me = client.get("/auth/me").json()
    response = uploader(
        name,
        mime,
        data,
        params={"tenant_id": "untrusted", "owner_user_id": "untrusted"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {
        "document_id",
        "name",
        "content_type",
        "created_at",
        "updated_at",
    }
    with sessions() as session:
        doc = session.get_one(Document, body["document_id"])
        assert (doc.name, doc.owner_user_id, doc.tenant_id) == (
            name,
            me["user_id"],
            me["tenant_id"],
        )
        assert (storage / doc.storage_key).read_bytes() == data
        assert name not in doc.storage_key
        chunks = sorted(doc.chunks, key=lambda c: c.chunk_index)
        assert [c.chunk_text for c in chunks] == chunk_text(text, 8, 2)
        assert all(
            c.tenant_id == doc.tenant_id and c.token_count == len(c.chunk_text.split())
            for c in chunks
        )
    assert client.get(f"/documents/{body['document_id']}").status_code == 200


@pytest.mark.parametrize(
    ("name", "mime", "data", "status"),
    [
        ("x.exe", "text/plain", b"words", 415),
        ("x.txt", "application/pdf", b"words", 415),
        ("x.txt", "text/plain", b"%PDF-1.4\nhello", 422),
        ("x.txt", "text/plain", b"words\x00binary", 422),
        ("x.txt", "text/plain", b"\xff", 422),
        ("x.txt", "text/plain", b"", 422),
        ("x.txt", "text/plain", b" \r\n", 422),
        ("x.txt", "text/plain", b"x" * 4097, 413),
        ("../x.txt", "text/plain", b"words", 422),
        ("x.pdf", "application/pdf", b"plain words", 422),
        ("x.pdf", "application/pdf", b"%PDF-1.7\nbroken\n%%EOF", 422),
        ("x.pdf", "application/pdf", pdf_bytes(), 422),
        ("x.pdf", "application/pdf", pdf_bytes(text=True, encrypted=True), 422),
    ],
)
def test_rejected_upload_leaves_no_file_or_records(
    uploader, sessions, storage, name, mime, data, status
):
    response = uploader(name, mime, data)
    assert response.status_code == status
    assert str(storage) not in response.text
    assert not list(storage.rglob("*"))
    with sessions() as session:
        assert session.scalar(select(Document.id)) is None
        assert session.scalar(select(DocumentChunk.id)) is None


def test_upload_requires_bearer(client, storage):
    response = client.post(
        "/documents/upload",
        content=b"words",
        headers={"X-Filename": "private.txt", "Content-Type": "text/plain"},
    )
    assert response.status_code == 401
    assert not storage.exists()


def test_retries_and_tenant_isolation(uploader, sessions, client, storage):
    def send(data):
        response = uploader("same.txt", "text/plain", data)
        assert response.status_code == 201
        return response.json()["document_id"]

    first = send(b"one")
    duplicate = send(b"one")
    with sessions.begin() as session:
        user = User(
            tenant=Tenant(name="Other"),
            email="other@example.test",
            password_hash="unused",
        )
        session.add(user)
        session.flush()
        token = create_access_token(user_id=user.id, tenant_id=user.tenant_id)
    client.headers["Authorization"] = f"Bearer {token}"
    other = send(b"two")
    assert client.get(f"/documents/{first}").status_code == 404
    assert [d["document_id"] for d in client.get("/documents").json()] == [other]
    with sessions() as session:
        paths = [
            storage / session.get_one(Document, i).storage_key
            for i in (first, duplicate, other)
        ]
        assert len(set(paths)) == 3
        assert paths[0].parent != paths[2].parent
        assert [p.read_bytes() for p in paths] == [b"one", b"one", b"two"]


@pytest.mark.parametrize("failure", ["fsync", "commit", "chunk"])
def test_processing_failure_cleanup(uploader, sessions, storage, monkeypatch, failure):
    def fail(*args):
        raise SQLAlchemyError("private internal detail")

    if failure == "fsync":
        monkeypatch.setattr(upload.os, "fsync", fail)
    elif failure == "commit":
        monkeypatch.setattr(Session, "commit", fail)
    else:
        monkeypatch.setattr(upload, "chunk_text", fail)
    response = uploader("x.txt", "text/plain", b"letters")
    assert response.status_code == 500
    assert "private internal detail" not in response.text
    assert not any(p.is_file() for p in storage.rglob("*"))
    with sessions() as session:
        assert session.scalar(select(Document.id)) is None
        assert session.scalar(select(DocumentChunk.id)) is None


def test_lost_commit_acknowledgement_preserves_committed_file(
    uploader, sessions, storage, monkeypatch
):
    commit = Session.commit

    def lose_ack(session):
        commit(session)
        raise SQLAlchemyError("lost acknowledgement")

    monkeypatch.setattr(Session, "commit", lose_ack)
    response = uploader("x.txt", "text/plain", b"letters")
    assert response.status_code == 500
    with sessions() as session:
        doc = session.scalars(select(Document)).one()
        assert (storage / doc.storage_key).read_bytes() == b"letters"
        assert doc.chunks[0].chunk_text == "letters"


def test_window_budgets_before_slicing():
    class Unsliceable(str):
        def __getitem__(self, key):
            raise AssertionError("Allocated a window before checking budget")

    with pytest.raises(ValueError, match="output budget"):
        chunk_text(Unsliceable("a" * 1_000_000), 8192, 6144)
    with pytest.raises(ValueError, match="too many"):
        chunk_text(Unsliceable("a" * 4097), 1, 0)
    with pytest.raises(ValueError, match="75%"):
        Settings(_env_file=None, chunk_size=8192, chunk_overlap=6145)


@pytest.mark.parametrize("level", ["ancestor", "root", "tenant"])
def test_linked_storage_rejected(tmp_path, level):
    parent = tmp_path / "ancestor"
    root = parent / "root"
    namespace = "a" * 64
    link = {"ancestor": parent, "root": root, "tenant": root / namespace}[level]
    link.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        with tenant_directory(root, namespace):
            pytest.fail("Accepted a linked directory")
    assert not list(outside.iterdir())


def test_directory_path_manipulation(tmp_path):
    with pytest.raises(ValueError):
        with tenant_directory(tmp_path, "../other"):
            pytest.fail("Accepted traversal")
    with pytest.raises(ValueError):
        with tenant_directory(tmp_path / ".." / "other", "a" * 64):
            pytest.fail("Accepted traversal")


@pytest.mark.parametrize(
    "failure", ["extraction", "persistence", "rollback", "reconciliation", "cleanup"]
)
def test_safe_upload_diagnostics(uploader, caplog, monkeypatch, failure):
    caplog.set_level("INFO", logger="uvicorn.error")

    def fail(*args, **kwargs):
        raise OSError("private-exception-marker")

    data = b"private-content-marker"
    if failure == "extraction":
        data += b"\x00"
    elif failure == "persistence":
        monkeypatch.setattr(upload, "create_document_metadata", fail)
    elif failure == "rollback":
        monkeypatch.setattr(upload, "create_document_metadata", fail)
        monkeypatch.setattr(Session, "rollback", fail)
    elif failure == "reconciliation":
        monkeypatch.setattr(Session, "commit", fail)
        monkeypatch.setattr(Session, "scalar", fail)
    else:
        monkeypatch.setattr(upload.os, "fsync", fail)
        monkeypatch.setattr(upload.os, "unlink", fail)
    response = uploader("private-filename-marker.txt", "text/plain", data)
    assert response.status_code == (422 if failure == "extraction" else 500)
    records = [
        json.loads(r.getMessage()) for r in caplog.records if r.name == "uvicorn.error"
    ]
    assert {r["correlation_id"] for r in records} == {
        response.headers["X-Correlation-ID"]
    }
    assert {r["stage"] for r in records} >= {"admission", failure, "request"}
    assert all(
        set(r) <= {"event", "stage", "outcome", "status", "correlation_id"}
        for r in records
    )
    assert "private-" not in caplog.text
