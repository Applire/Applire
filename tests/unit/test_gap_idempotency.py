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

"""E037 PQ #3 — match-score stability.

The deterministic score (fit_weight x status-factor) wobbled 99->98->97 for a
single application because every screen re-POSTed gap analysis and _run_analysis
ALWAYS re-ran the LLM (a fresh per-requirement classification) and inserted a new
gap_analyses row. Different screens then read different rows.

Fix: analyze_gaps is idempotent per (job, profile-fingerprint) — it reuses the
latest row instead of re-running the LLM when inputs are unchanged, and the
/gaps/refresh path clamps the headline score monotonically up (added evidence
never lowers it).
"""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.flow import FlowSession
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.models.user import User
from applire.providers.llm.mock import MockLLMProvider
from applire.services.gap import analyze_gaps

from tests.support.profile_factory import make_master_profile, set_profile_json

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")


class _SpyProvider(MockLLMProvider):
    """MockLLMProvider that counts aparse_json calls (LLM invocations)."""

    def __init__(self) -> None:
        self.parse_calls = 0

    async def aparse_json(self, prompt, **kwargs):  # type: ignore[override]
        self.parse_calls += 1
        return await super().aparse_json(prompt, **kwargs)


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user           # noqa: F401
    import applire.models.job            # noqa: F401
    import applire.models.profile        # noqa: F401
    import applire.models.gap            # noqa: F401
    import applire.models.cv             # noqa: F401
    import applire.models.cover_letter   # noqa: F401
    import applire.models.session        # noqa: F401
    import applire.models.flow           # noqa: F401
    import applire.models.application     # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company        # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.uploads        # noqa: F401

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
    """Seed user + job + profile + a flow (no gap analysis yet)."""
    user = User(
        id=_STUB_USER_ID,
        email="local@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="hash-idem",
        raw_text="Senior Python Engineer",
        role_title="Senior Python Engineer",
        required_skills=["Python", "FastAPI"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="DE",
    )
    profile = make_master_profile(id=uuid.uuid4(), profile_json=_profile_json())
    db.add_all([user, job, profile])
    await db.commit()

    flow = FlowSession(
        user_id=user.id,
        job_id=job.id,
        current_step="gap_analysis",
        user_type="new",
        available_actions={"next": "interview", "skip": "cv_generation"},
    )
    db.add(flow)
    await db.commit()
    await db.refresh(flow)
    return job, profile, flow


async def _count_rows(db, job_id) -> int:
    rows = (
        await db.execute(
            select(GapAnalysis).where(GapAnalysis.job_analysis_id == job_id)
        )
    ).scalars().all()
    return len(rows)


@pytest.mark.asyncio
async def test_second_analyze_reuses_row_without_calling_llm(db, seeded):
    job, profile, flow = seeded
    spy = _SpyProvider()

    r1 = await analyze_gaps(job.id, db, spy)
    calls_after_first = spy.parse_calls
    assert calls_after_first > 0, "first run must invoke the LLM"
    assert await _count_rows(db, job.id) == 1

    r2 = await analyze_gaps(job.id, db, spy)

    # Same (job, profile) → SAME row, no new insert, no extra LLM call.
    assert r2.id == r1.id, "unchanged inputs must reuse the existing analysis row"
    assert spy.parse_calls == calls_after_first, "reuse must NOT call the LLM again"
    assert await _count_rows(db, job.id) == 1, "no duplicate gap_analyses row"
    assert r2.match_score == r1.match_score, "same inputs → same score"


@pytest.mark.asyncio
async def test_changed_profile_fingerprint_recomputes(db, seeded):
    job, profile, flow = seeded
    spy = _SpyProvider()

    r1 = await analyze_gaps(job.id, db, spy)
    calls_after_first = spy.parse_calls

    # Genuinely change the profile content (e.g. interview enrichment).
    new_json = _profile_json()
    new_json["skills"].append(
        {"name": "FastAPI", "category": "technical", "proficiency": "advanced"}
    )
    set_profile_json(profile, new_json)
    await db.commit()

    r2 = await analyze_gaps(job.id, db, spy)

    assert r2.id != r1.id, "a changed profile must produce a new analysis row"
    assert spy.parse_calls > calls_after_first, "changed inputs must re-run the LLM"
    assert await _count_rows(db, job.id) == 2


@pytest.mark.asyncio
async def test_flow_fk_and_latest_converge_on_reuse(db, seeded):
    job, profile, flow = seeded
    spy = _SpyProvider()

    r1 = await analyze_gaps(job.id, db, spy)
    r2 = await analyze_gaps(job.id, db, spy)  # idempotent reuse

    latest = (
        await db.execute(
            select(GapAnalysis)
            .where(GapAnalysis.job_analysis_id == job.id)
            .order_by(desc(GapAnalysis.created_at))
            .limit(1)
        )
    ).scalar_one()
    refreshed_flow = (
        await db.execute(select(FlowSession).where(FlowSession.id == flow.id))
    ).scalar_one()

    # The flow-pinned FK and the latest-by-created_at read path must be the SAME
    # row the analyze call returned — every screen reads one score.
    assert r2.id == latest.id
    assert refreshed_flow.gap_analysis_id == latest.id


@pytest.mark.asyncio
async def test_refresh_clamps_score_monotonically_up(db, seeded):
    job, profile, flow = seeded
    spy = _SpyProvider()

    r1 = await analyze_gaps(job.id, db, spy)

    # Simulate a previously-displayed high score, then a genuine profile change
    # so the refresh path recomputes (fingerprint differs).
    g1 = (
        await db.execute(select(GapAnalysis).where(GapAnalysis.id == r1.id))
    ).scalar_one()
    g1.match_score = 0.99
    new_json = _profile_json()
    new_json["personal_info"]["headline"] = "changed"
    set_profile_json(profile, new_json)
    await db.commit()

    # /gaps/refresh re-evaluates after new evidence — must never lower the score.
    r2 = await analyze_gaps(job.id, db, spy, clamp_to_previous=True)

    assert r2.id != r1.id, "refresh with changed inputs creates a new row"
    assert r2.match_score is not None
    assert r2.match_score >= 0.99, "added evidence must never lower the headline score"
