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

"""US167 — POST /api/profile/staged/{id}/resolve endpoint (error mapping)."""
import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base
    from applire.models.profile import MasterProfile
    from applire.models.uploads import UploadRecord
    from applire.models.user import User

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[MasterProfile.__table__, UploadRecord.__table__, User.__table__],
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


async def _park(db_session, name="Marcus Weber", company="SAP", gate="name_divergence"):
    from applire.models.uploads import UploadRecord

    rec = UploadRecord(
        original_filename="cv.pdf",
        content_hash="x",
        mime_type="application/pdf",
        file_path="/tmp/cv.pdf",
        byte_size=10,
        gate_status=gate,
        staged_extraction={
            "personal_info": {"name": name},
            "work_experience": [{"company": company, "role": "Eng", "start_date": "2020-01"}],
            "skills": [{"name": "Python", "category": "technical"}],
        },
    )
    db_session.add(rec)
    await db_session.commit()
    await db_session.refresh(rec)
    return rec.id


@pytest.mark.asyncio
async def test_resolve_unknown_id_returns_404(client):
    resp = await client.post(
        f"/api/profile/staged/{uuid.uuid4()}/resolve", json={"action": "merge"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_resolve_merge_returns_200(client, db_session):
    staged_id = await _park(db_session)
    resp = await client.post(
        f"/api/profile/staged/{staged_id}/resolve", json={"action": "merge"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "merge"
    assert body["profile_id"] is not None


@pytest.mark.asyncio
async def test_resolve_twice_returns_409(client, db_session):
    staged_id = await _park(db_session)
    await client.post(f"/api/profile/staged/{staged_id}/resolve", json={"action": "discard"})
    resp = await client.post(
        f"/api/profile/staged/{staged_id}/resolve", json={"action": "merge"}
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_resolve_invalid_action_returns_422(client, db_session):
    staged_id = await _park(db_session)
    resp = await client.post(
        f"/api/profile/staged/{staged_id}/resolve", json={"action": "bogus"}
    )
    assert resp.status_code == 422  # Pydantic rejects the Literal
