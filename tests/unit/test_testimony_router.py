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

"""#258 — POST /api/profile/testimony: the UI door for free-text testimony."""
import uuid
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile


class _Provider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        self.calls += 1
        return self.payload


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base
    from applire.models.profile import MasterProfile
    from applire.models.user import User
    from applire.models.user_settings import UserSettings

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    MasterProfile.__table__,
                    User.__table__,
                    UserSettings.__table__,
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session):
    from unittest.mock import AsyncMock, MagicMock

    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.profile import router, _get_provider

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session

    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
    app.dependency_overrides[get_auth_provider] = lambda: auth

    provider = _Provider(
        {
            "ops": [{"op": "upsert_skill", "name": "Kafka", "category": "technical"}],
            "ambiguities": [],
            "denials": [],
        }
    )
    app.dependency_overrides[_get_provider] = lambda: provider

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.provider = provider  # type: ignore[attr-defined]
        yield ac


async def _seed_profile(db_session, profile_json=None):
    db_session.add(
        make_master_profile(
            profile_json=profile_json
            or {"personal_info": {"full_name": "Daniel Kovač"}, "metadata": {}}
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_testimony_applies_and_returns_receipts(client, db_session):
    await _seed_profile(db_session)

    resp = await client.post(
        "/api/profile/testimony",
        json={"text": "I ran Kafka in production for three years."},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "applied"
    assert body["schema_version"] == "testimony/1"
    assert len(body["changes"]) == 1


@pytest.mark.asyncio
async def test_testimony_rejects_empty_text(client, db_session):
    await _seed_profile(db_session)

    resp = await client.post("/api/profile/testimony", json={"text": ""})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_testimony_without_profile_returns_404(client):
    resp = await client.post(
        "/api/profile/testimony", json={"text": "Something about me."}
    )

    assert resp.status_code == 404
