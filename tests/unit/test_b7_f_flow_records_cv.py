# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Hazelnut build 7, WP-F — ADR-016 amended 2026-11-07.

789:248: ``flow_sessions.generated_cv_id`` advanced only in the BROWSER after the
CV page saw a generation finish, so regenerating and leaving the page left the
application (and the letter, which reads its CV through the flow) on the old
CV. The web door now schedules ``record_generated_cv`` after the render task.

789:254: after a reopened interview the CV page showed the OLD CV as current.
The flow state now carries ``cv_summary.predates_interview`` — the letter's L-1
predicate applied to the CV.

In-process against a real ``sqlite+aiosqlite`` session; no LLM.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.owners import HARNESS_USER_ID
from tests.support.posting_links import link_posting
from tests.support.profile_factory import make_master_profile

T0 = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def db():
    import importlib
    import pkgutil

    import applire.models  # noqa: F401

    for _m in pkgutil.iter_modules(applire.models.__path__):
        importlib.import_module(f"applire.models.{_m.name}")
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _session_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def _seed(db, *, history=None):
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    user = User(id=HARNESS_USER_ID, email=f"felix-{uuid.uuid4()}@example.org")
    job = JobAnalysis(
        raw_text_hash=f"hash-{uuid.uuid4()}",
        raw_text="Backend engineer, payments",
        role_title="Backend Engineer",
        seniority_level="senior",
        language_requirement="English",
    )
    profile = make_master_profile(
        profile_json={
            "personal_info": {},
            "work_experience": [],
            "metadata": {"enrichment_history": history or []},
        }
    )
    db.add_all([user, job, profile])
    await db.flush()
    await link_posting(db, job, user.id)
    await db.commit()
    return user, job, profile


async def _cv(db, job, profile, *, created_at, status="ready", user_id=None, deleted=False):
    from applire.models.cv import GeneratedCV

    cv = GeneratedCV(
        job_analysis_id=job.id,
        profile_id=profile.id,
        tailored_data={},
        status=status,
    )
    if user_id is not None:
        cv.user_id = user_id
    cv.created_at = created_at
    if deleted:
        cv.deleted_at = created_at
    db.add(cv)
    await db.commit()
    await db.refresh(cv)
    return cv


async def _flow(db, user, job, step, cv=None):
    from applire.models.flow import FlowSession

    flow = FlowSession(
        user_id=user.id,
        job_id=job.id,
        current_step=step,
        user_type="returning",
        available_actions={},
        generated_cv_id=cv.id if cv is not None else None,
    )
    db.add(flow)
    await db.commit()
    await db.refresh(flow)
    return flow


async def _record(db, cv_id):
    from applire.services.flow import orchestrator

    with patch.object(orchestrator, "AsyncSessionLocal", return_value=_session_cm(db)):
        await orchestrator.record_generated_cv(cv_id, user_id=HARNESS_USER_ID)


async def _reload(db, flow):
    await db.refresh(flow)
    return flow


# ---------------------------------------------------------------------------
# 789:248 — the web door records its CV server-side
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_first_cv_finished_after_the_page_was_left_is_recorded_and_completes(db):
    """The page was left during the generation: nothing in the browser advanced
    the flow. The background task records the CV and moves cv_generation →
    complete, as the page would have."""
    user, job, profile = await _seed(db)
    flow = await _flow(db, user, job, "cv_generation")
    cv = await _cv(db, job, profile, created_at=T0)

    await _record(db, cv.id)

    flow = await _reload(db, flow)
    assert flow.generated_cv_id == cv.id
    assert flow.current_step == "complete"


@pytest.mark.asyncio
async def test_a_regenerated_cv_replaces_the_old_one_on_a_complete_flow(db):
    """THE 789:248 shape: regenerate on a complete flow, leave the page."""
    user, job, profile = await _seed(db)
    old = await _cv(db, job, profile, created_at=T0)
    flow = await _flow(db, user, job, "complete", cv=old)
    new = await _cv(db, job, profile, created_at=T0 + timedelta(minutes=30))

    await _record(db, new.id)

    flow = await _reload(db, flow)
    assert flow.generated_cv_id == new.id
    assert flow.current_step == "complete"


@pytest.mark.asyncio
async def test_an_older_generation_finishing_late_never_displaces_a_newer_cv(db):
    """Mutation kill: drop the `created_at` guard in `_record_ready_cv`."""
    user, job, profile = await _seed(db)
    older = await _cv(db, job, profile, created_at=T0)
    newer = await _cv(db, job, profile, created_at=T0 + timedelta(minutes=5))
    flow = await _flow(db, user, job, "complete", cv=newer)

    await _record(db, older.id)

    flow = await _reload(db, flow)
    assert flow.generated_cv_id == newer.id


@pytest.mark.asyncio
async def test_a_reopened_interview_keeps_its_step_and_cv(db):
    """The person went back into the interview meanwhile: no write."""
    user, job, profile = await _seed(db)
    old = await _cv(db, job, profile, created_at=T0)
    flow = await _flow(db, user, job, "interview", cv=old)
    new = await _cv(db, job, profile, created_at=T0 + timedelta(minutes=30))

    await _record(db, new.id)

    flow = await _reload(db, flow)
    assert flow.current_step == "interview"
    assert flow.generated_cv_id == old.id


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["interview", "gap_analysis", "jd_analysis"])
async def test_the_write_half_leaves_any_other_step_alone(db, step):
    """The step guard itself, without the task's catch-all: at a step that does
    not record a CV the write half returns cleanly and writes nothing. Mutation
    kill: drop the `current_step not in (...)` clause (the advance then raises
    ArtifactNotFoundError, which the task-level test above would swallow)."""
    from applire.services.flow.orchestrator import _record_ready_cv

    user, job, profile = await _seed(db)
    old = await _cv(db, job, profile, created_at=T0)
    flow = await _flow(db, user, job, step, cv=old)
    new = await _cv(db, job, profile, created_at=T0 + timedelta(minutes=30))

    await _record_ready_cv(new, HARNESS_USER_ID, db)

    flow = await _reload(db, flow)
    assert flow.current_step == step
    assert flow.generated_cv_id == old.id


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "generating", "pending"])
async def test_a_cv_that_is_not_ready_is_not_recorded(db, status):
    user, job, profile = await _seed(db)
    old = await _cv(db, job, profile, created_at=T0)
    flow = await _flow(db, user, job, "complete", cv=old)
    bad = await _cv(db, job, profile, created_at=T0 + timedelta(minutes=30), status=status)

    await _record(db, bad.id)

    flow = await _reload(db, flow)
    assert flow.generated_cv_id == old.id


@pytest.mark.asyncio
async def test_a_deleted_or_foreign_cv_is_not_recorded(db):
    from applire.models.user import User

    user, job, profile = await _seed(db)
    other = User(id=uuid.uuid4(), email=f"other-{uuid.uuid4()}@example.org")
    db.add(other)
    other_profile = make_master_profile(
        user_id=other.id, profile_json={"personal_info": {}, "work_experience": []}
    )
    db.add(other_profile)
    await db.commit()
    old = await _cv(db, job, profile, created_at=T0)
    flow = await _flow(db, user, job, "complete", cv=old)
    deleted = await _cv(db, job, profile, created_at=T0 + timedelta(minutes=1), deleted=True)
    foreign = await _cv(
        db, job, other_profile, created_at=T0 + timedelta(minutes=2), user_id=other.id
    )

    await _record(db, deleted.id)
    await _record(db, foreign.id)

    flow = await _reload(db, flow)
    assert flow.generated_cv_id == old.id


@pytest.mark.asyncio
async def test_a_failure_inside_the_task_never_raises(db):
    """A background task that raises would only log noise; the browser path
    still records the CV when the page is open."""
    from applire.services.flow import orchestrator

    broken = MagicMock()
    broken.__aenter__ = AsyncMock(side_effect=RuntimeError("db down"))
    broken.__aexit__ = AsyncMock(return_value=False)
    with patch.object(orchestrator, "AsyncSessionLocal", return_value=broken):
        await orchestrator.record_generated_cv(uuid.uuid4(), user_id=HARNESS_USER_ID)


# ---------------------------------------------------------------------------
# The seam: POST /api/cv/generate schedules it AFTER the render task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_generate_schedules_the_record_after_the_render(db):
    """Seam test for the one call site (routers/cv.py::post_generate). Mutation
    kill: delete the `add_task(record_generated_cv, …)` line."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from applire.auth import get_auth_provider
    from applire.auth.no_auth import NoAuthProvider
    from applire.db.session import get_db
    from applire.routers import cv as cv_router_mod

    user, job, profile = await _seed(db)
    calls: list[str] = []

    async def fake_render(*args, **kwargs):
        calls.append("render")

    async def fake_record(cv_id, *, user_id):
        calls.append(f"record:{cv_id}:{user_id}")

    app = FastAPI()
    app.include_router(cv_router_mod.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_auth_provider] = lambda: NoAuthProvider()
    app.dependency_overrides[cv_router_mod._get_provider] = lambda: MagicMock()

    with patch("applire.services.cv._render_cv_background", fake_render), patch.object(
        cv_router_mod, "record_generated_cv", fake_record
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post("/api/cv/generate", json={"job_id": str(job.id)})

    assert resp.status_code == 201, resp.text
    cv_id = resp.json()["cv_id"]
    assert calls == ["render", f"record:{cv_id}:{HARNESS_USER_ID}"]


# ---------------------------------------------------------------------------
# 789:254 — cv_summary.predates_interview
# ---------------------------------------------------------------------------


def _record_at(ts: datetime, source: str = "interview", changes=None):
    return {
        "timestamp": ts.isoformat(),
        "source": source,
        "changes": changes
        if changes is not None
        else [{"section": "work_experience", "action": "added", "field": "responsibilities"}],
    }


async def _state(db, flow):
    from applire.services.flow.orchestrator import get_flow_state

    return await get_flow_state(flow.id, db, user_id=HARNESS_USER_ID)


@pytest.mark.asyncio
async def test_predates_interview_when_the_interview_added_a_fact_after_the_cv(db):
    user, job, profile = await _seed(db, history=[_record_at(T0 + timedelta(hours=1))])
    cv = await _cv(db, job, profile, created_at=T0)
    flow = await _flow(db, user, job, "cv_generation", cv=cv)

    state = await _state(db, flow)

    assert state.cv_summary is not None
    assert state.cv_summary.predates_interview is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        _record_at(T0 - timedelta(hours=1)),  # interview BEFORE the CV
        _record_at(T0 + timedelta(hours=1), source="cv_import"),  # not an interview door
        _record_at(T0 + timedelta(hours=1), changes=[]),  # an interview that added nothing
    ],
    ids=["interview-before-cv", "import-after-cv", "empty-interview-after-cv"],
)
async def test_predates_interview_is_false_otherwise(db, record):
    user, job, profile = await _seed(db, history=[record])
    cv = await _cv(db, job, profile, created_at=T0)
    flow = await _flow(db, user, job, "complete", cv=cv)

    state = await _state(db, flow)

    assert state.cv_summary is not None
    assert state.cv_summary.predates_interview is False


@pytest.mark.asyncio
async def test_a_new_version_after_the_interview_clears_the_cue(db):
    """The whole 789:254 loop at the service tier: interview after CV #1 →
    predates; CV #2 recorded by the web door → no longer predates."""
    user, job, profile = await _seed(db, history=[_record_at(T0 + timedelta(hours=1))])
    old = await _cv(db, job, profile, created_at=T0)
    flow = await _flow(db, user, job, "cv_generation", cv=old)
    assert (await _state(db, flow)).cv_summary.predates_interview is True

    new = await _cv(db, job, profile, created_at=T0 + timedelta(hours=2))
    await _record(db, new.id)

    state = await _state(db, flow)
    assert state.current_step == "complete"
    assert state.cv_summary.cv_id == new.id
    assert state.cv_summary.predates_interview is False
