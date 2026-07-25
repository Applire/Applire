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

"""#260 exit (b) router wiring — POST /api/job/{job_id}/gaps/liabilities/downgrade."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TEST_USER_ID = uuid.uuid4()


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base
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
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        job = JobAnalysis(
            id=uuid.uuid4(),
            raw_text_hash="hash-router-liability",
            raw_text="Senior ML Engineer",
            role_title="Senior ML Engineer",
            required_skills=["RAG"],
            nice_to_have_skills=[],
            keywords=[],
            seniority_level="senior",
            company_culture_signals=[],
            language_requirement="DE",
        )
        profile = MasterProfile(id=uuid.uuid4(), profile_json={"skills": [{"name": "RAG"}]})
        session.add_all([job, profile])
        await session.commit()

        gap_analysis = GapAnalysis(
            job_analysis_id=job.id,
            profile_id=profile.id,
            match_score=1.0,
            input_fingerprint="fp-router-liability",
            critical_gaps=[],
            minor_gaps=[],
            strengths=["RAG"],
            keyword_gaps=[],
            category_a=["RAG"],
            category_b=[],
            category_c=[],
            keyword_ledger=[
                {
                    "concept": "RAG", "surface_forms": ["RAG"], "sources": ["required"],
                    "fit_weight": 1.0, "status": "direct", "evidence": "listed under Skills",
                    "claimable": True, "narrative_backed": False,
                },
            ],
            requirement_breakdown=[],
            created_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        )
        session.add(gap_analysis)
        await session.commit()
        yield session, job.id
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session):
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.job import router

    session, job_id = db_session
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session

    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=TEST_USER_ID))
    app.dependency_overrides[get_auth_provider] = lambda: auth

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, job_id


@pytest.mark.asyncio
async def test_downgrade_endpoint_flips_the_ledger_entry(client):
    ac, job_id = client
    resp = await ac.post(
        f"/api/job/{job_id}/gaps/liabilities/downgrade", json={"concept": "RAG"}
    )
    assert resp.status_code == 200
    body = resp.json()
    by_concept = {e["concept"]: e for e in body["keyword_ledger"]}
    assert by_concept["RAG"]["claimable"] is False
    assert body["match_score"] < 1.0


@pytest.mark.asyncio
async def test_downgrade_endpoint_404_for_unknown_job(client):
    ac, _job_id = client
    resp = await ac.post(
        f"/api/job/{uuid.uuid4()}/gaps/liabilities/downgrade", json={"concept": "RAG"}
    )
    assert resp.status_code == 404
