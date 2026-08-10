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

"""US168 — POST /api/profile/undo-last-merge endpoint (merge→undo round-trip)."""
import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base
    from applire.models.profile import MasterProfile, ProfileSnapshot
    from applire.models.user import User

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    MasterProfile.__table__,
                    ProfileSnapshot.__table__,
                    User.__table__,
                ],
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


async def _seed_merge(db_session):
    """A profile post-merge (head=E1) with a pre-merge snapshot keyed to E1."""
    from applire.models.profile import MasterProfile, ProfileSnapshot

    pre = {"_marker": "pre", "metadata": {"enrichment_history": [{"id": "E0"}], "pending_conflicts": []}}
    post = {"_marker": "post", "metadata": {"enrichment_history": [{"id": "E1"}], "pending_conflicts": [{"x": 1}]}}
    profile = make_master_profile(profile_json=post)
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    db_session.add(
        ProfileSnapshot(profile_id=profile.id, enrichment_record_id="E1", profile_json=pre)
    )
    await db_session.commit()
    return profile


@pytest.mark.asyncio
async def test_undo_endpoint_restores_pre_merge_profile(client, db_session):
    from applire.models.profile import MasterProfile

    profile = await _seed_merge(db_session)

    resp = await client.post("/api/profile/undo-last-merge")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"restored": True, "discarded_later_edits": False}

    refreshed = (await db_session.execute(select(MasterProfile))).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.profile_json["_marker"] == "pre"


@pytest.mark.asyncio
async def test_undo_endpoint_with_nothing_to_undo_returns_false(client):
    resp = await client.post("/api/profile/undo-last-merge")
    assert resp.status_code == 200
    assert resp.json() == {"restored": False, "discarded_later_edits": False}
