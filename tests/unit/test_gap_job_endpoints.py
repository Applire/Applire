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

"""Async gap-analysis endpoints (E037 N2).

POST /api/job/{job_id}/gap-jobs → 202 handle; GET /api/job/{job_id}/gap-jobs/{id} → poll.
The sync POST /api/job/{job_id}/gaps is removed (405). The background task is patched out
so these stay unit tests (no real analyze_gaps / LLM).
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TEST_USER_ID = uuid.uuid4()


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base
    from applire.models.gap import GapAnalysis
    from applire.models.gap_job import GapAnalysisJob

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[GapAnalysisJob.__table__, GapAnalysis.__table__]
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session):
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.job import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session

    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=TEST_USER_ID))
    app.dependency_overrides[get_auth_provider] = lambda: auth

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_post_gap_jobs_returns_202_and_handle(client):
    job_id = uuid.uuid4()
    # Patch the background task so no real analyze_gaps runs after the response.
    with patch("applire.routers.job.run_gap_job_background", new=AsyncMock()):
        resp = await client.post(f"/api/job/{job_id}/gap-jobs")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert uuid.UUID(body["gap_job_id"])  # valid uuid


@pytest.mark.asyncio
async def test_get_gap_job_poll_pending(client):
    job_id = uuid.uuid4()
    with patch("applire.routers.job.run_gap_job_background", new=AsyncMock()):
        start = await client.post(f"/api/job/{job_id}/gap-jobs")
    gap_job_id = start.json()["gap_job_id"]

    poll = await client.get(f"/api/job/{job_id}/gap-jobs/{gap_job_id}")
    assert poll.status_code == 200
    data = poll.json()
    assert data["status"] == "pending"
    assert data["result"] is None


@pytest.mark.asyncio
async def test_get_gap_job_ready_returns_analysis(client, db_session):
    from applire.models.gap import GapAnalysis
    from applire.models.gap_job import GapAnalysisJob, GapJobStatus

    job_id = uuid.uuid4()
    analysis = GapAnalysis(
        job_analysis_id=job_id,
        profile_id=uuid.uuid4(),
        match_score=0.72,
    )
    db_session.add(analysis)
    await db_session.commit()
    await db_session.refresh(analysis)

    gap_job = GapAnalysisJob(
        job_analysis_id=job_id,
        user_id=TEST_USER_ID,
        status=GapJobStatus.ready.value,
        result_gap_analysis_id=analysis.id,
    )
    db_session.add(gap_job)
    await db_session.commit()
    await db_session.refresh(gap_job)

    poll = await client.get(f"/api/job/{job_id}/gap-jobs/{gap_job.id}")
    assert poll.status_code == 200
    data = poll.json()
    assert data["status"] == "ready"
    assert data["result"]["id"] == str(analysis.id)
    assert data["result"]["match_score"] == pytest.approx(0.72)


@pytest.mark.asyncio
async def test_get_gap_job_unknown_is_404(client):
    job_id = uuid.uuid4()
    poll = await client.get(f"/api/job/{job_id}/gap-jobs/{uuid.uuid4()}")
    assert poll.status_code == 404


@pytest.mark.asyncio
async def test_sync_post_gaps_removed(client):
    job_id = uuid.uuid4()
    resp = await client.post(f"/api/job/{job_id}/gaps")
    assert resp.status_code == 405  # method no longer allowed (GET /gaps still exists)
