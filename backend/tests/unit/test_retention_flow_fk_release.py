# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The calendar-TTL purges release the flow's pointers first (W3 real-boot finding).

``flow_sessions`` points at its interview session, CV and cover letter with plain
FKs (no ON DELETE). On Postgres the calendar purges of those rows aborted with a
ForeignKeyViolation — and because the worker only catches Programming/Operational
errors, the whole ``python -m applire.retention`` run exited 1 and every later
rule was skipped. SQLite enforces FKs only with ``PRAGMA foreign_keys=ON``, which
this test turns on, so the regression is visible here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import applire.models  # noqa: F401
from applire.db.session import Base
from applire.models.cover_letter import GeneratedCoverLetter
from applire.models.cv import GeneratedCV
from applire.models.flow import FlowSession
from applire.models.session import InterviewSession
from applire.models.job import JobAnalysis
from applire.models.user import User
from tests.support.profile_factory import make_master_profile

OWNER = uuid.UUID("cccccccc-0000-0000-0000-00000000000c")


@pytest_asyncio.fixture
async def fk_db():
    eng = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(eng.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    past = datetime.now(timezone.utc) - timedelta(days=400)
    async with factory() as s:
        s.add(User(id=OWNER, email="c@example.org"))
        job = JobAnalysis(
            raw_text_hash=uuid.uuid4().hex, raw_text="t", role_title="R",
            seniority_level="mid", language_requirement="English",
        )
        s.add(job)
        await s.flush()
        profile = make_master_profile(profile_json={})
        s.add(profile)
        await s.flush()
        cv = GeneratedCV(job_analysis_id=job.id, profile_id=profile.id,
                         tailored_data={}, expires_at=past)
        cl = GeneratedCoverLetter(job_analysis_id=job.id, profile_id=profile.id,
                                  expires_at=past)
        iv = InterviewSession(job_analysis_id=job.id, profile_id=profile.id, state={})
        s.add_all([cv, cl, iv])
        await s.flush()
        flow = FlowSession(user_id=OWNER, job_id=job.id, generated_cv_id=cv.id,
                           generated_cover_letter_id=cl.id, interview_session_id=iv.id)
        s.add(flow)
        await s.flush()
        await s.execute(
            text("UPDATE interview_sessions SET updated_at = :t"), {"t": past}
        )
        await s.commit()
        ids = {"cv": cv.id, "cl": cl.id, "iv": iv.id, "flow": flow.id}
    yield factory, ids
    await eng.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rule, model, pointer",
    [
        ("_purge_cvs", GeneratedCV, "generated_cv_id"),
        ("_purge_cover_letters", GeneratedCoverLetter, "generated_cover_letter_id"),
        ("_purge_sessions", InterviewSession, "interview_session_id"),
    ],
)
async def test_an_expired_document_a_flow_points_at_is_purged(fk_db, rule, model, pointer):
    from applire.retention import worker

    factory, ids = fk_db
    async with factory() as s:
        assert await getattr(worker, rule)(s) == 1
        assert (await s.execute(select(model.id))).first() is None
        flow = await s.get(FlowSession, ids["flow"])
        assert getattr(flow, pointer) is None
