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

"""E039 / US218 — Application status pipeline (step P2).

Covers:
  - UserStatus enum carries the full pipeline: tracking, applied, interviewing,
    offer, rejected, hired
  - patch_application accepts user_status=interviewing
  - list_applications filters by the new value

No Docker, no LLM.
"""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.application import UserStatus
from applire.schemas.application import (
    CreateApplicationRequest,
    PatchApplicationRequest,
)
from applire.services.application import (
    create_application,
    list_applications,
    patch_application,
)

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Enum contract
# ---------------------------------------------------------------------------


def test_userstatus_has_interviewing():
    assert UserStatus.interviewing.value == "interviewing"


def test_userstatus_member_set_is_full_pipeline():
    assert {m.value for m in UserStatus} == {
        "tracking", "applied", "interviewing", "offer", "rejected", "hired",
        "cancelled",
    }


def test_patch_request_accepts_interviewing():
    req = PatchApplicationRequest(user_status=UserStatus.interviewing)
    assert req.user_status == UserStatus.interviewing


# ---------------------------------------------------------------------------
# Service round-trip
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered (pattern of test_application_service.py)."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.user        # noqa: F401
    import applire.models.job         # noqa: F401
    import applire.models.profile     # noqa: F401
    import applire.models.gap         # noqa: F401
    import applire.models.cv          # noqa: F401
    import applire.models.session     # noqa: F401
    import applire.models.flow        # noqa: F401
    import applire.models.uploads     # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _make_job():
    from applire.models.job import JobAnalysis

    return JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Head of Department at Example AG",
        role_title="Head of Department",
        company_name="Example AG",
        required_skills=["Leadership"],
        nice_to_have_skills=[],
        keywords=["Leadership"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="DE",
    )


@pytest_asyncio.fixture
async def user_and_jobs(db):
    """Insert a stub user and two job analyses; return (user, [job1, job2])."""
    from applire.models.user import User

    user = User(
        id=_STUB_USER_ID,
        email="local@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    jobs = [_make_job(), _make_job()]
    db.add_all([user, *jobs])
    await db.commit()
    return user, jobs


@pytest.mark.asyncio
async def test_patch_sets_interviewing(db, user_and_jobs):
    _, jobs = user_and_jobs
    created = await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=jobs[0].id), db
    )
    patched = await patch_application(
        created.id, _STUB_USER_ID, PatchApplicationRequest(user_status=UserStatus.interviewing), db
    )
    assert patched.user_status == UserStatus.interviewing


@pytest.mark.asyncio
async def test_list_filters_by_interviewing(db, user_and_jobs):
    _, jobs = user_and_jobs
    first = await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=jobs[0].id), db
    )
    await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=jobs[1].id), db
    )
    await patch_application(
        first.id, _STUB_USER_ID, PatchApplicationRequest(user_status=UserStatus.interviewing), db
    )

    result = await list_applications(
        _STUB_USER_ID, db, user_status=UserStatus.interviewing
    )
    assert len(result.items) == 1
    assert result.items[0].id == first.id
    assert result.items[0].user_status == UserStatus.interviewing


# ---------------------------------------------------------------------------
# Agent collector #676: the first transition to `applied` stamps applied_at on
# every door (the stamp used to live only in the browser's PATCH body)
# ---------------------------------------------------------------------------


async def _new_app(db, job):
    return await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db
    )


@pytest.mark.asyncio
async def test_applied_without_a_date_stamps_applied_at(db, user_and_jobs):
    _, jobs = user_and_jobs
    created = await _new_app(db, jobs[0])
    assert created.applied_at is None
    before = datetime.now(timezone.utc)
    patched = await patch_application(
        created.id, _STUB_USER_ID, PatchApplicationRequest(user_status=UserStatus.applied), db
    )
    assert patched.applied_at is not None
    stamped = patched.applied_at
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    assert stamped >= before.replace(microsecond=0)


@pytest.mark.asyncio
async def test_applied_never_overwrites_an_existing_applied_at(db, user_and_jobs):
    _, jobs = user_and_jobs
    created = await _new_app(db, jobs[0])
    original = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    await patch_application(
        created.id, _STUB_USER_ID, PatchApplicationRequest(applied_at=original), db
    )
    patched = await patch_application(
        created.id, _STUB_USER_ID, PatchApplicationRequest(user_status=UserStatus.applied), db
    )
    assert patched.applied_at.replace(tzinfo=timezone.utc) == original


@pytest.mark.asyncio
async def test_a_caller_supplied_applied_at_wins_over_the_stamp(db, user_and_jobs):
    _, jobs = user_and_jobs
    created = await _new_app(db, jobs[0])
    sent = datetime(2026, 2, 14, 12, 0, tzinfo=timezone.utc)
    patched = await patch_application(
        created.id, _STUB_USER_ID,
        PatchApplicationRequest(user_status=UserStatus.applied, applied_at=sent), db,
    )
    assert patched.applied_at.replace(tzinfo=timezone.utc) == sent
    # an explicit null in the same body is the caller's choice too — no stamp
    other = await _new_app(db, jobs[1])
    patched = await patch_application(
        other.id, _STUB_USER_ID,
        PatchApplicationRequest(user_status=UserStatus.applied, applied_at=None), db,
    )
    assert patched.applied_at is None


@pytest.mark.asyncio
async def test_other_statuses_do_not_stamp_applied_at(db, user_and_jobs):
    _, jobs = user_and_jobs
    created = await _new_app(db, jobs[0])
    patched = await patch_application(
        created.id, _STUB_USER_ID, PatchApplicationRequest(user_status=UserStatus.interviewing), db
    )
    assert patched.applied_at is None


@pytest.mark.asyncio
async def test_seam_mcp_update_application_applied_stamps_applied_at(db, user_and_jobs):
    """The agent door, through the REAL service (only the session is injected):
    ``update_application(user_status="applied")`` used to leave applied_at null."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from applire.mcp.server import update_application

    _, jobs = user_and_jobs
    created = await _new_app(db, jobs[0])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server._current_user_id", AsyncMock(return_value=_STUB_USER_ID)),
    ):
        result = await update_application(application_id=str(created.id), user_status="applied")
    assert result["user_status"] == "applied"
    assert result["applied_at"] is not None


@pytest.mark.asyncio
async def test_seam_rest_patch_applied_without_a_date_stamps_applied_at(db, user_and_jobs):
    """The browser door, through the real router: a status-only PATCH (the
    dossier's status control sends no applied_at) now records the moment too."""
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.application import router

    _, jobs = user_and_jobs
    created = await _new_app(db, jobs[0])
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=_STUB_USER_ID))
    app.dependency_overrides[get_auth_provider] = lambda: auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(f"/api/applications/{created.id}", json={"user_status": "applied"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied_at"] is not None
