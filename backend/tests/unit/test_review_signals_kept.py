# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#702 — *So lassen* / *Keep it as is* on a cross-document item, through the REAL
review router + ``services/review_signals.py`` + ``review_state`` over an in-memory
SQLite DB (same harness shape as ``test_review_endpoints.py``).

Also pins the review_state invariants the ``critic:`` producer must not break:
a cross-document decision never counts as a group-1 row (ADR-090 cl. 6's
"k of n decided") and never collides with a group-1 decision of equal fold.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.auth import get_auth_provider
from applire.auth.no_auth import NoAuthProvider
from applire.db.session import get_db
from tests.support.profile_factory import make_master_profile

import applire.routers.review as review_router
import applire.services.review_state as rs

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests" / "files" / "review_signals" / "critic-report-2026-09-13-marcus.json"
)


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _client(db) -> TestClient:
    async def _override_get_db():
        yield db

    app = FastAPI()
    app.dependency_overrides[get_auth_provider] = lambda: NoAuthProvider()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[review_router._get_provider] = lambda: object()
    app.include_router(review_router.router)
    return TestClient(app, raise_server_exceptions=True)


async def _seed_letter(db, *, critic_report=None, review_state=None) -> uuid.UUID:
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.job import JobAnalysis

    job_id, profile_id, cl_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.add(JobAnalysis(
        id=job_id, raw_text_hash=str(job_id), raw_text="JD text", role_title="Leiter Operations",
        required_skills=[], nice_to_have_skills=[], keywords=[], seniority_level="lead",
        company_culture_signals=[], language_requirement="de",
    ))
    db.add(make_master_profile(id=profile_id, profile_json={}))
    db.add(GeneratedCoverLetter(
        id=cl_id, job_analysis_id=job_id, profile_id=profile_id, template="classic_german",
        letter_data={"body": {"paragraphs": ["Absatz."]}}, status="ready", document_language="de",
        critic_report=critic_report, review_state=review_state,
    ))
    await db.commit()
    return cl_id


def _report() -> dict:
    return json.loads(FIXTURE.read_text())["report"]


def _transfer_key() -> str:
    from applire.schemas.outcome_critic import OutcomeCriticReport

    return OutcomeCriticReport.model_validate(_report()).cross_document[0].key


@pytest.mark.asyncio
async def test_kept_records_the_decision_on_a_listed_item(db):
    cl_id = await _seed_letter(db, critic_report=_report())
    key = _transfer_key()
    resp = _client(db).post(f"/api/cover-letter/{cl_id}/review/kept", json={"finding_key": key})
    assert resp.status_code == 200, resp.text
    (decision,) = resp.json()["review_state"]["decisions"]
    assert decision["action"] == "kept"
    assert decision["finding_key"] == key
    assert decision["label"].startswith("Hygiene- und Dokumentationsdisziplin")

    from applire.models.cover_letter import GeneratedCoverLetter

    row = await db.get(GeneratedCoverLetter, cl_id)
    assert row.review_state["decisions"][0]["action"] == "kept"


@pytest.mark.asyncio
async def test_kept_false_withdraws_it(db):
    key = _transfer_key()
    state = rs.with_decision({}, key, "x", "kept")
    cl_id = await _seed_letter(db, critic_report=_report(), review_state=state)
    resp = _client(db).post(
        f"/api/cover-letter/{cl_id}/review/kept", json={"finding_key": key, "keep": False}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["review_state"]["decisions"] == []


@pytest.mark.asyncio
async def test_kept_on_an_unlisted_item_is_409_and_writes_nothing(db):
    cl_id = await _seed_letter(db, critic_report=_report())
    resp = _client(db).post(
        f"/api/cover-letter/{cl_id}/review/kept", json={"finding_key": "critic:a sentence nobody wrote"}
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "finding_not_listed"
    from applire.models.cover_letter import GeneratedCoverLetter

    assert (await db.get(GeneratedCoverLetter, cl_id)).review_state is None


@pytest.mark.asyncio
async def test_kept_with_a_group_one_key_is_422(db):
    cl_id = await _seed_letter(db, critic_report=_report())
    resp = _client(db).post(f"/api/cover-letter/{cl_id}/review/kept", json={"finding_key": "ats:kubernetes"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_kept_on_a_foreign_document_is_404(db):
    resp = _client(db).post(
        f"/api/cover-letter/{uuid.uuid4()}/review/kept", json={"finding_key": _transfer_key()}
    )
    assert resp.status_code == 404


# ── review_state invariants with the `critic` producer ───────────────────────


def test_a_critic_decision_never_counts_as_a_group_one_row():
    state = rs.with_decision({}, "critic:some sentence", "x", "kept")
    derived = rs.derive_review(state, {"keywords": {}}, {"claims": []})
    assert derived["decided"] == [] and derived["total"] == 0


def test_a_critic_decision_and_a_group_one_decision_of_equal_fold_do_not_collide():
    state = rs.with_decision({}, "ats:supply chain", "Supply Chain", "taken_out")
    state = rs.with_decision(state, "critic:supply chain", "Supply Chain.", "kept")
    actions = sorted(d["action"] for d in state["decisions"])
    assert actions == ["kept", "taken_out"]
    assert rs.get_decision(state, "ats:supply chain")["action"] == "taken_out"
    assert rs.get_decision(state, "critic:supply chain")["action"] == "kept"


def test_a_critic_key_never_names_a_group_one_finding():
    finding = rs.GroupOneFinding(key="ats:supply chain", producer="ats", norm="supply chain", label="Supply Chain")
    assert rs.find_listed([finding], "critic:supply chain") is None
    assert rs.find_listed([finding], "ats:supply chain") is finding
