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

"""US160 — GET /api/profile/health endpoint (round-trip over the latest profile)."""
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base
    from applire.models.profile import MasterProfile
    from applire.models.user import User

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[MasterProfile.__table__, User.__table__]
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
    from applire.routers.profile import router
    from unittest.mock import AsyncMock, MagicMock

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session

    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
    app.dependency_overrides[get_auth_provider] = lambda: auth

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_profile(db_session, profile_json):
    from applire.models.profile import MasterProfile

    db_session.add(MasterProfile(profile_json=profile_json))
    await db_session.commit()


@pytest.mark.asyncio
async def test_health_reports_a_conflict_issue(client, db_session):
    profile_json = {
        "personal_info": {"name": "Marcus Weber"},
        "work_experience": [{"company": "BMW", "role": "Engineer", "start_date": "2020-01"}],
        "metadata": {
            "pending_conflicts": [
                {
                    "section": "work_experience",
                    "field": "start_date",
                    "existing_value": "2020-01",
                    "incoming_value": "2019-06",
                    "source": "cv:audi.pdf",
                }
            ],
            "enrichment_history": [],
        },
    }
    await _seed_profile(db_session, profile_json)

    resp = await client.get("/api/profile/health")

    assert resp.status_code == 200
    body = resp.json()
    conflicts = [i for i in body["issues"] if i["thread"] == "conflict"]
    assert len(conflicts) == 1
    assert conflicts[0]["profile_mismatch_severity"] == "review"
    assert conflicts[0]["field_ref"] == "start_date"
    assert "completeness" in body
    assert 0.0 <= body["completeness"]["score"] <= 1.0
    assert "education" in body["completeness"]["gaps"]


@pytest.mark.asyncio
async def test_health_with_no_profile_returns_empty(client):
    resp = await client.get("/api/profile/health")
    assert resp.status_code == 200
    assert resp.json() == {"issues": [], "completeness": {"score": 0.0, "gaps": [], "field_gaps": []}}
