# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""E039 / US220 — Duplicate-JD recognition (journey Branch F).

find_duplicate_application checks a freshly analyzed JD against the USER'S
applications only (job_analyses stay shared/global and are never scanned
across users — epic 4.1 boundary). Conservative first cut (journey OQ #10
owns any fuzzy threshold later):

  1. same job_analysis row      — upstream analyze_jd dedup (same URL or
                                  identical text hash) resolved to a job the
                                  user already has in the pipeline
  2. near-exact source_url      — normalized URL match (scheme/www/tracking
                                  params/trailing slash ignored) against the
                                  sibling job's URL or the dossier source_url
  3. near-exact JD text         — whitespace/case-normalized equality

The hint never blocks anything; it is a read-model enrichment on the analyze
response. No Docker, no LLM.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.schemas.application import CreateApplicationRequest
from applire.services.application import create_application, find_duplicate_application

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_OTHER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")

_JD_TEXT_A = (
    "Senior Data Analyst at DataCraft GmbH.\n"
    "Requirements: SQL, Python, Tableau, 5 years experience in analytics.\n"
    "Location: Berlin. Hybrid work model."
)

# Same vocabulary, different company and location — must NOT count as duplicate.
_JD_TEXT_SIMILAR = (
    "Senior Data Analyst at InsightWorks AG.\n"
    "Requirements: SQL, Python, Tableau, 5 years experience in analytics.\n"
    "Location: Munich. Hybrid work model."
)


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.user        # noqa: F401
    import applire.models.job         # noqa: F401
    import applire.models.profile     # noqa: F401
    import applire.models.gap         # noqa: F401
    import applire.models.cv          # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session     # noqa: F401
    import applire.models.flow        # noqa: F401
    import applire.models.uploads     # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _make_job(**overrides):
    from applire.models.job import JobAnalysis

    defaults = dict(
        id=uuid.uuid4(),
        raw_text_hash=f"hash-{uuid.uuid4()}",
        raw_text=_JD_TEXT_A,
        role_title="Senior Data Analyst",
        company_name="DataCraft GmbH",
        required_skills=["SQL", "Python"],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="German",
    )
    defaults.update(overrides)
    return JobAnalysis(**defaults)


@pytest_asyncio.fixture
async def user(db):
    from applire.models.user import User

    u = User(id=_STUB_USER_ID, email="stub@applire.local")
    db.add(u)
    await db.commit()
    return u


async def _add_application(db, job, user_id=_STUB_USER_ID, **create_kwargs):
    db.add(job)
    await db.commit()
    return await create_application(
        user_id,
        CreateApplicationRequest(job_analysis_id=job.id, **create_kwargs),
        db,
    )


# ---------------------------------------------------------------------------
# Rule 1: same job_analysis row (upstream URL/text-hash dedup already collapsed it)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_job_row_is_flagged_as_duplicate(db, user):
    job = _make_job(source_url="https://stepstone.de/jobs/123")
    app = await _add_application(db, job)

    hint = await find_duplicate_application(
        _STUB_USER_ID,
        job_analysis_id=job.id,
        source_url="https://stepstone.de/jobs/123",
        raw_text=_JD_TEXT_A,
        db=db,
    )
    assert hint is not None
    assert hint.application_id == app.id
    assert hint.job_analysis_id == job.id
    assert hint.matched_on == "job"
    assert hint.company_name == "DataCraft GmbH"
    assert hint.role_title == "Senior Data Analyst"
    assert hint.analyzed_at is not None


# ---------------------------------------------------------------------------
# Rule 2: near-exact source_url (repost on the same board, tracking noise)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_url_with_tracking_params_matches(db, user):
    job = _make_job(source_url="https://www.stepstone.de/jobs/123")
    app = await _add_application(db, job)

    new_job = _make_job(raw_text="Completely rewritten posting text for the same job.")
    db.add(new_job)
    await db.commit()

    hint = await find_duplicate_application(
        _STUB_USER_ID,
        job_analysis_id=new_job.id,
        source_url="http://stepstone.de/jobs/123/?utm_source=linkedin&utm_medium=social",
        raw_text="Completely rewritten posting text for the same job.",
        db=db,
    )
    assert hint is not None
    assert hint.application_id == app.id
    assert hint.matched_on == "source_url"


@pytest.mark.asyncio
async def test_dossier_source_url_matches_text_tab_capture(db, user):
    """Emma pasted text + a source link (US216 text tab); job row has no URL."""
    job = _make_job(source_url=None)
    app = await _add_application(db, job, source_url="https://linkedin.com/jobs/view/456")

    new_job = _make_job(raw_text="Fresh repost wording, different enough text.")
    db.add(new_job)
    await db.commit()

    hint = await find_duplicate_application(
        _STUB_USER_ID,
        job_analysis_id=new_job.id,
        source_url="https://www.linkedin.com/jobs/view/456/",
        raw_text="Fresh repost wording, different enough text.",
        db=db,
    )
    assert hint is not None
    assert hint.application_id == app.id
    assert hint.matched_on == "source_url"


@pytest.mark.asyncio
async def test_different_url_same_board_is_not_duplicate(db, user):
    job = _make_job(source_url="https://stepstone.de/jobs/123")
    await _add_application(db, job)

    new_job = _make_job(raw_text=_JD_TEXT_SIMILAR)
    db.add(new_job)
    await db.commit()

    hint = await find_duplicate_application(
        _STUB_USER_ID,
        job_analysis_id=new_job.id,
        source_url="https://stepstone.de/jobs/999",
        raw_text=_JD_TEXT_SIMILAR,
        db=db,
    )
    assert hint is None


# ---------------------------------------------------------------------------
# Rule 3: near-exact JD text on a different URL (cross-board repost)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_text_different_url_matches(db, user):
    job = _make_job(source_url="https://stepstone.de/jobs/123")
    app = await _add_application(db, job)

    # Same posting on another board: identical wording, whitespace/case noise.
    reposted_text = "  senior data analyst AT DataCraft GmbH.\nRequirements:  SQL, Python, Tableau, 5 years experience in analytics.\nLocation: Berlin.   Hybrid work model. "
    new_job = _make_job(raw_text=reposted_text, source_url="https://indeed.com/viewjob?jk=abc")
    db.add(new_job)
    await db.commit()

    hint = await find_duplicate_application(
        _STUB_USER_ID,
        job_analysis_id=new_job.id,
        source_url="https://indeed.com/viewjob?jk=abc",
        raw_text=reposted_text,
        db=db,
    )
    assert hint is not None
    assert hint.application_id == app.id
    assert hint.matched_on == "text"


@pytest.mark.asyncio
async def test_similar_but_different_role_is_not_duplicate(db, user):
    """Genuinely similar JD at a different company — no false positive."""
    job = _make_job()
    await _add_application(db, job)

    new_job = _make_job(
        raw_text=_JD_TEXT_SIMILAR,
        company_name="InsightWorks AG",
    )
    db.add(new_job)
    await db.commit()

    hint = await find_duplicate_application(
        _STUB_USER_ID,
        job_analysis_id=new_job.id,
        source_url=None,
        raw_text=_JD_TEXT_SIMILAR,
        db=db,
    )
    assert hint is None


# ---------------------------------------------------------------------------
# Scope boundaries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_deleted_application_is_ignored(db, user):
    from applire.services.application import delete_application

    job = _make_job(source_url="https://stepstone.de/jobs/123")
    app = await _add_application(db, job)
    await delete_application(app.id, db)

    hint = await find_duplicate_application(
        _STUB_USER_ID,
        job_analysis_id=job.id,
        source_url="https://stepstone.de/jobs/123",
        raw_text=_JD_TEXT_A,
        db=db,
    )
    assert hint is None


@pytest.mark.asyncio
async def test_other_users_application_is_ignored(db, user):
    """Per-user boundary: another user's pipeline never leaks into the hint."""
    from applire.models.user import User

    db.add(User(id=_OTHER_USER_ID, email="other@applire.local"))
    await db.commit()

    job = _make_job(source_url="https://stepstone.de/jobs/123")
    await _add_application(db, job, user_id=_OTHER_USER_ID)

    hint = await find_duplicate_application(
        _STUB_USER_ID,
        job_analysis_id=job.id,
        source_url="https://stepstone.de/jobs/123",
        raw_text=_JD_TEXT_A,
        db=db,
    )
    assert hint is None


@pytest.mark.asyncio
async def test_no_applications_returns_none(db, user):
    job = _make_job()
    db.add(job)
    await db.commit()

    hint = await find_duplicate_application(
        _STUB_USER_ID,
        job_analysis_id=job.id,
        source_url=None,
        raw_text=_JD_TEXT_A,
        db=db,
    )
    assert hint is None


@pytest.mark.asyncio
async def test_most_recent_matching_application_wins(db, user):
    """Two applications could match (edge); the most recently updated one is offered."""
    older_job = _make_job(source_url="https://stepstone.de/jobs/123")
    older = await _add_application(db, older_job)

    newer_job = _make_job(
        raw_text=_JD_TEXT_A + " ",  # same normalized text, distinct hash row
        source_url="https://indeed.com/viewjob?jk=abc",
    )
    newer = await _add_application(db, newer_job)

    # Make ordering deterministic.
    from applire.models.application import Application

    older_row = await db.get(Application, older.id)
    newer_row = await db.get(Application, newer.id)
    older_row.updated_at = datetime.now(timezone.utc) - timedelta(days=5)
    newer_row.updated_at = datetime.now(timezone.utc)
    await db.commit()

    probe_job = _make_job(raw_text=_JD_TEXT_A + "  ")
    db.add(probe_job)
    await db.commit()

    hint = await find_duplicate_application(
        _STUB_USER_ID,
        job_analysis_id=probe_job.id,
        source_url=None,
        raw_text=_JD_TEXT_A + "  ",
        db=db,
    )
    assert hint is not None
    assert hint.application_id == newer.id


# ---------------------------------------------------------------------------
# Endpoint wiring: POST /api/job/analyze enriches the response for the user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_endpoint_carries_duplicate_of(db, user):
    """The router resolves the current user and enriches the analyze response —
    re-analyzing a pipelined job's text must surface the Branch F hint."""
    import hashlib
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    from applire.db.session import get_db
    from applire.main import app as fastapi_app
    from applire.routers.job import _get_provider

    # analyze_jd's text-hash dedup must resolve to THIS row (exact repost case).
    real_hash = hashlib.sha256(_JD_TEXT_A.encode()).hexdigest()
    job = _make_job(raw_text=_JD_TEXT_A, raw_text_hash=real_hash)
    application = await _add_application(db, job)

    async def _override_db():
        yield db

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value={})  # dedup hit → LLM never called

    fastapi_app.dependency_overrides[get_db] = _override_db
    fastapi_app.dependency_overrides[_get_provider] = lambda: provider
    try:
        client = TestClient(fastapi_app)
        res = client.post("/api/job/analyze", json={"text": _JD_TEXT_A})
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)
        fastapi_app.dependency_overrides.pop(_get_provider, None)

    assert res.status_code == 200
    body = res.json()
    assert body["duplicate_of"] is not None
    assert body["duplicate_of"]["application_id"] == str(application.id)
    assert body["duplicate_of"]["matched_on"] == "job"
    provider.aparse_json.assert_not_called()


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


def test_job_analysis_response_defaults_duplicate_of_to_none():
    from applire.schemas.job import JobAnalysisResponse

    response = JobAnalysisResponse(
        id=uuid.uuid4(),
        role_title="Senior Data Analyst",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="German",
        raw_text_hash="abc",
    )
    assert response.duplicate_of is None
