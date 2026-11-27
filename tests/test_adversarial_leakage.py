"""Exercise both HTTP boundaries for every demo persona."""

from secrets import token_urlsafe

import pytest
from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.db.models import (
    Document,
    DocumentChunk,
    DocumentPermission,
    Role,
    Tenant,
    User,
    UserRole,
)
from scripts import demo_runtime
from scripts.demo_runtime import local_runtime
from scripts.run_benchmarks import CASES, probe, revoke_trial
from scripts.seed_demo import COMPANIES, PERSONAS, prepare


@pytest.fixture(scope="module")
def demo():
    with local_runtime() as rt:
        yield rt, prepare(rt, seed=True)


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("persona", PERSONAS)
@pytest.mark.parametrize("company", COMPANIES)
def test_forbidden_sources_absent(demo, company, persona, case):
    rt, corpus = demo
    for observed in probe(rt, corpus, company, persona, case):
        assert not observed.leaked, (observed.endpoint, company, persona, case)


@pytest.mark.parametrize("company", COMPANIES)
def test_revoke_same_hr_token(demo, company):
    rt, corpus = demo
    after, _ = revoke_trial(rt, corpus, company)
    assert all(not o.leaked for o in after)


def test_seed_rerun_preserves_records_and_vectors(demo):
    rt, corpus = demo
    again = prepare(rt, seed=True)
    assert again.entries == corpus.entries
    with rt.sessions() as session:
        for model, expected in [
            (Tenant, 2),
            (User, 6),
            (Role, 6),
            (UserRole, 6),
            (Document, 6),
            (DocumentChunk, 8),
            (DocumentPermission, 8),
        ]:
            assert session.scalar(select(func.count()).select_from(model)) == expected
    assert rt.store.client.count(rt.store.collection, exact=True).count == 8
    assert sum(p.is_file() for p in get_settings().upload_dir.rglob("*")) == 6


def test_staging_guard_rejects_application_database_before_io(monkeypatch):
    config = Settings(
        _env_file=None,
        app_env="staging",
        database_url="mysql+pymysql://unused.invalid/rag_db",
        qdrant_collection_name="rag_demo_chunks",
    )
    monkeypatch.setattr(demo_runtime, "get_settings", lambda: config)
    monkeypatch.setenv("DEMO_PASSWORD_KEY", token_urlsafe(32))
    monkeypatch.setattr(demo_runtime.httpx, "Client", pytest.fail)
    monkeypatch.setattr(demo_runtime, "create_engine", pytest.fail)
    with pytest.raises(ValueError, match="Staging demo targets"):
        with demo_runtime.staging_runtime("http://127.0.0.1:18000"):
            pytest.fail("application database accepted")
