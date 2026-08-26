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

"""ADR-077 clause 6 — the REST pins subresource (additive, never list-replace).

POST /api/applications/{id}/pins       — add one pin, fail-closed (422)
DELETE /api/applications/{id}/pins/{pin_id} — idempotent removal (204)
GET /api/applications/{id}             — read model carries pinned_facts
"""

import uuid

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.schemas.profile import MasterProfileData, Skill, WorkEntry

TEST_USER_ID = uuid.uuid4()

ACHIEVEMENT = "Cut deployment time by 70% across 12 teams"


@pytest_asyncio.fixture
async def scene():
    from applire.db.session import Base
    import applire.models.user           # noqa: F401
    import applire.models.job            # noqa: F401
    import applire.models.profile        # noqa: F401
    import applire.models.gap            # noqa: F401
    import applire.models.cv             # noqa: F401
    import applire.models.cover_letter   # noqa: F401
    import applire.models.session        # noqa: F401
    import applire.models.flow           # noqa: F401
    import applire.models.application    # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company        # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.uploads        # noqa: F401
    from applire.models.application import Application
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile, authorized_profile_write

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        profile_data = MasterProfileData(
            work_experience=[
                WorkEntry(role="Lead", company="Acme", achievements=[ACHIEVEMENT])
            ],
            skills=[Skill(name="Kubernetes")],
        )
        with authorized_profile_write():
            profile = MasterProfile(
                profile_json=profile_data.model_dump(mode="json")
            )
            session.add(profile)
        job = JobAnalysis(
            id=uuid.uuid4(),
            raw_text_hash=f"hash-{uuid.uuid4()}",
            raw_text="JD",
            role_title="Lead",
            company_name="X",
            required_skills=[],
            nice_to_have_skills=[],
            keywords=[],
            seniority_level="Senior",
            company_culture_signals=[],
            language_requirement="German",
        )
        session.add(job)
        await session.flush()
        application = Application(user_id=TEST_USER_ID, job_analysis_id=job.id)
        session.add(application)
        await session.commit()
        yield session, application.id, profile_data
    await engine.dispose()


@pytest_asyncio.fixture
async def client(scene):
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.application import router

    session, app_id, profile_data = scene
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session

    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=TEST_USER_ID))
    app.dependency_overrides[get_auth_provider] = lambda: auth

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, app_id, profile_data


@pytest.mark.asyncio
async def test_post_pin_creates_and_get_exposes_it(client):
    ac, app_id, profile = client
    resp = await ac.post(
        f"/api/applications/{app_id}/pins",
        json={
            "entry_type": "work",
            "entry_id": profile.work_experience[0].id,
            "quote": ACHIEVEMENT,
        },
    )
    assert resp.status_code == 201
    pin = resp.json()
    assert pin["quote"] == ACHIEVEMENT and pin["pin_id"]

    read = await ac.get(f"/api/applications/{app_id}")
    assert read.status_code == 200
    assert [p["pin_id"] for p in read.json()["pinned_facts"]] == [pin["pin_id"]]


@pytest.mark.asyncio
async def test_post_pin_422_when_quote_does_not_resolve(client):
    ac, app_id, profile = client
    resp = await ac.post(
        f"/api/applications/{app_id}/pins",
        json={
            "entry_type": "work",
            "entry_id": profile.work_experience[0].id,
            "quote": "Grew revenue by 300%",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_pin_422_for_a_cv_target_on_a_volunteer_entry(client):
    """ADR-077 amended 2026-08-26 (#580, clause 1 correction / SF-PIN.9): the CV
    renders no volunteering or publication section, so a `cv` target on such an
    entry is a pin that could never fire — refused before anything is read."""
    ac, app_id, _profile = client
    resp = await ac.post(
        f"/api/applications/{app_id}/pins",
        json={"entry_type": "volunteer", "entry_id": "any", "quote": "q", "targets": ["cv"]},
    )
    assert resp.status_code == 422
    assert "CV has no section" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_post_pin_404_for_unknown_application(client):
    ac, _app_id, profile = client
    resp = await ac.post(
        f"/api/applications/{uuid.uuid4()}/pins",
        json={
            "entry_type": "work",
            "entry_id": profile.work_experience[0].id,
            "quote": ACHIEVEMENT,
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_pin_removes_and_is_idempotent(client):
    ac, app_id, profile = client
    created = await ac.post(
        f"/api/applications/{app_id}/pins",
        json={
            "entry_type": "skill",
            "entry_id": profile.skills[0].id,
            "quote": "Kubernetes",
        },
    )
    pin_id = created.json()["pin_id"]

    first = await ac.delete(f"/api/applications/{app_id}/pins/{pin_id}")
    assert first.status_code == 204
    second = await ac.delete(f"/api/applications/{app_id}/pins/{pin_id}")
    assert second.status_code == 204

    read = await ac.get(f"/api/applications/{app_id}")
    assert read.json()["pinned_facts"] == []
