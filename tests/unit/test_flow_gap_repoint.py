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

"""Regression test (UAT 2026-06-26): the flow's gap_analysis_id FK must follow
the latest gap analysis on recompute.

Bug: the CV page reads match_score + gaps from flow.gap_analysis_id, but that FK
was only written when advance_flow transitioned INTO gap_analysis. Later
recomputes (/gaps/refresh, interview-completion, gap-click) created NEW
gap_analyses rows but never repointed the flow FK, so the CV page kept showing
the stale pre-interview score and re-listed already-answered gaps.

Fix: analyze_gaps() (the single GapAnalysis creation seam — all recompute paths
route through it) now repoints the owning non-deleted flow to the newest analysis.
"""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.flow import FlowSession
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.models.user import User
from applire.providers.llm.mock import MockLLMProvider
from applire.services.gap import analyze_gaps

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user       # noqa: F401
    import applire.models.job        # noqa: F401
    import applire.models.profile    # noqa: F401
    import applire.models.gap        # noqa: F401
    import applire.models.cv         # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session    # noqa: F401
    import applire.models.flow       # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.uploads    # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _profile_json() -> dict:
    return {
        "work_experience": [
            {"company": "Acme", "role": "Dev", "start_date": "2018-01"}
        ],
        "education": [
            {"institution": "TU Berlin", "degree": "BSc", "field": "CS"}
        ],
        "skills": [
            {"name": "Python", "category": "technical", "proficiency": "expert"}
        ],
        "languages": [{"language": "German", "level": "native"}],
        "personal_info": {
            "first_name": "Max",
            "last_name": "Muster",
            "email": "max@test.de",
        },
        "professional_summary": {"de": "Entwickler", "en": "Developer"},
        "certifications": [],
        "publications": [],
        "volunteer_activities": [],
    }


@pytest_asyncio.fixture
async def seeded(db):
    """Seed user + job + profile + an existing gap analysis G1 + a flow pinned to G1."""
    user = User(
        id=_STUB_USER_ID,
        email="local@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="hash-repoint",
        raw_text="Senior Python Engineer",
        role_title="Senior Python Engineer",
        required_skills=["Python", "FastAPI"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="DE",
    )
    profile = MasterProfile(id=uuid.uuid4(), profile_json=_profile_json())
    db.add_all([user, job, profile])
    await db.commit()

    # G1 — the original pre-interview analysis (low score)
    g1 = GapAnalysis(
        id=uuid.uuid4(),
        job_analysis_id=job.id,
        profile_id=profile.id,
        match_score=0.40,
        critical_gaps=["FastAPI"],
        minor_gaps=[],
        strengths=[],
        keyword_gaps=[],
        category_a=["Python"],
        category_b=["FastAPI"],
        category_c=[],
        requirement_breakdown=[],
    )
    db.add(g1)
    await db.commit()

    flow = FlowSession(
        user_id=user.id,
        job_id=job.id,
        current_step="gap_analysis",
        user_type="new",
        available_actions={"next": "interview", "skip": "cv_generation"},
        gap_analysis_id=g1.id,
    )
    db.add(flow)
    await db.commit()
    await db.refresh(flow)
    return job, profile, flow, g1


@pytest.mark.asyncio
async def test_recompute_repoints_flow_to_latest_gap_analysis(db, seeded):
    job, profile, flow, g1 = seeded

    # Recompute via the production creation path (what /gaps/refresh and
    # interview-completion both call).
    response = await analyze_gaps(job.id, db, MockLLMProvider())
    g2_id = response.id

    assert g2_id != g1.id, "a new gap analysis row should have been created"

    refreshed = (
        await db.execute(select(FlowSession).where(FlowSession.id == flow.id))
    ).scalar_one()
    assert refreshed.gap_analysis_id == g2_id, (
        "flow FK must follow the newest gap analysis, not stay pinned to G1"
    )


@pytest.mark.asyncio
async def test_recompute_does_not_change_flow_step(db, seeded):
    """Repointing the FK must NOT advance the step machine as a side effect."""
    job, profile, flow, g1 = seeded
    await analyze_gaps(job.id, db, MockLLMProvider())
    refreshed = (
        await db.execute(select(FlowSession).where(FlowSession.id == flow.id))
    ).scalar_one()
    assert refreshed.current_step == "gap_analysis"


@pytest.mark.asyncio
async def test_recompute_skips_deleted_flow(db, seeded):
    """A soft-deleted flow must not be repointed."""
    job, profile, flow, g1 = seeded
    flow.deleted_at = datetime.now(timezone.utc)
    await db.commit()

    await analyze_gaps(job.id, db, MockLLMProvider())

    refreshed = (
        await db.execute(select(FlowSession).where(FlowSession.id == flow.id))
    ).scalar_one()
    assert refreshed.gap_analysis_id == g1.id, "deleted flow FK must stay untouched"


@pytest.mark.asyncio
async def test_recompute_no_flow_is_noop(db, seeded):
    """No owning flow for the job → analyze_gaps still succeeds (null-safe)."""
    job, profile, flow, g1 = seeded
    # Point the flow at a different job so none owns `job`.
    flow.job_id = uuid.uuid4()
    await db.commit()

    response = await analyze_gaps(job.id, db, MockLLMProvider())
    assert response.id is not None
