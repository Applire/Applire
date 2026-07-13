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

"""US222 / issue #158 — Cancel an application (journey Branch I).

Covers (ADR-005 amendment 2026-07-13):
  - UserStatus gains the terminal value 'cancelled'
  - cancelling via PATCH shortens the inactivity timer to
    CANCELLED_APPLICATION_TTL_DAYS (default 7)
  - while cancelled, further updates re-arm the SHORT clock, not the 730d one
  - restoring (any other user_status) re-arms the standard inactivity clock
  - CANCELLED_APPLICATION_TTL_DAYS=0 disables the short clock
  - start_application_workflow refuses a cancelled application (409 semantics)
  - create_application reuse of a cancelled application restores it to tracking
  - mark_application_hired on a cancelled application re-arms the normal clock
  - the stale-CV nudge treats 'cancelled' as terminal
  - ApplicationResponse exposes expires_at (the UI's "will be removed on" date)

No Docker, no LLM.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.application import UserStatus
from applire.schemas.application import (
    CreateApplicationRequest,
    PatchApplicationRequest,
)
from applire.services.application import (
    ConflictError,
    create_application,
    mark_application_hired,
    patch_application,
    start_application_workflow,
)

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Enum / schema / constant contract
# ---------------------------------------------------------------------------


def test_userstatus_has_cancelled():
    assert UserStatus.cancelled.value == "cancelled"


def test_userstatus_member_set_includes_cancelled():
    assert {m.value for m in UserStatus} == {
        "tracking", "applied", "interviewing", "offer", "rejected", "hired",
        "cancelled",
    }


def test_patch_request_accepts_cancelled():
    req = PatchApplicationRequest(user_status=UserStatus.cancelled)
    assert req.user_status == UserStatus.cancelled


def test_cancelled_ttl_constant_defaults_to_seven_days():
    from applire.constants import CANCELLED_APPLICATION_TTL_DAYS

    assert CANCELLED_APPLICATION_TTL_DAYS == 7


def test_cancelled_is_a_stale_cv_terminal_status():
    from applire.services.application import _STALE_CV_TERMINAL_STATUSES

    assert UserStatus.cancelled.value in _STALE_CV_TERMINAL_STATUSES


# ---------------------------------------------------------------------------
# Fixtures (pattern of test_application_status_pipeline.py)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
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
async def user_and_job(db):
    from applire.models.user import User

    user = User(
        id=_STUB_USER_ID,
        email="local@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = _make_job()
    db.add_all([user, job])
    await db.commit()
    return user, job


def _days_from_now(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - datetime.now(timezone.utc)) / timedelta(days=1)


async def _fetch_expires_at(db, application_id) -> datetime:
    from applire.models.application import Application

    row = await db.get(Application, application_id)
    return row.expires_at


# ---------------------------------------------------------------------------
# Retention clock semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_shortens_expiry_to_cancelled_ttl(db, user_and_job):
    _, job = user_and_job
    created = await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db
    )
    await patch_application(
        created.id, _STUB_USER_ID,
        PatchApplicationRequest(user_status=UserStatus.cancelled), db,
    )
    days = _days_from_now(await _fetch_expires_at(db, created.id))
    assert 6.9 < days < 7.1


@pytest.mark.asyncio
async def test_update_while_cancelled_keeps_short_clock(db, user_and_job):
    _, job = user_and_job
    created = await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db
    )
    await patch_application(
        created.id, _STUB_USER_ID,
        PatchApplicationRequest(user_status=UserStatus.cancelled), db,
    )
    # A later dossier edit must NOT stretch the clock back to 730 days.
    await patch_application(
        created.id, _STUB_USER_ID, PatchApplicationRequest(notes="a note"), db
    )
    days = _days_from_now(await _fetch_expires_at(db, created.id))
    assert 6.9 < days < 7.1


@pytest.mark.asyncio
async def test_restore_rearms_standard_inactivity_clock(db, user_and_job):
    _, job = user_and_job
    created = await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db
    )
    await patch_application(
        created.id, _STUB_USER_ID,
        PatchApplicationRequest(user_status=UserStatus.cancelled), db,
    )
    await patch_application(
        created.id, _STUB_USER_ID,
        PatchApplicationRequest(user_status=UserStatus.tracking), db,
    )
    days = _days_from_now(await _fetch_expires_at(db, created.id))
    assert days > 700


@pytest.mark.asyncio
async def test_cancelled_ttl_zero_disables_short_clock(db, user_and_job, monkeypatch):
    import applire.services.application as app_service

    monkeypatch.setattr(app_service, "_CANCELLED_TTL_DAYS", 0)
    _, job = user_and_job
    created = await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db
    )
    await patch_application(
        created.id, _STUB_USER_ID,
        PatchApplicationRequest(user_status=UserStatus.cancelled), db,
    )
    days = _days_from_now(await _fetch_expires_at(db, created.id))
    assert days > 700


@pytest.mark.asyncio
async def test_mark_hired_on_cancelled_rearms_normal_clock(db, user_and_job):
    _, job = user_and_job
    created = await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db
    )
    await patch_application(
        created.id, _STUB_USER_ID,
        PatchApplicationRequest(user_status=UserStatus.cancelled), db,
    )
    result = await mark_application_hired(created.id, _STUB_USER_ID, db)
    assert result.user_status == UserStatus.hired.value
    days = _days_from_now(await _fetch_expires_at(db, created.id))
    assert days > 700


# ---------------------------------------------------------------------------
# Guards & reuse semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_workflow_refuses_cancelled_application(db, user_and_job):
    _, job = user_and_job
    created = await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db
    )
    await patch_application(
        created.id, _STUB_USER_ID,
        PatchApplicationRequest(user_status=UserStatus.cancelled), db,
    )
    with pytest.raises(ConflictError):
        await start_application_workflow(created.id, _STUB_USER_ID, db)


@pytest.mark.asyncio
async def test_create_reuse_of_cancelled_application_restores_it(db, user_and_job):
    """Re-adding the same job (duplicate-JD 'continue' path) must re-engage the
    application — otherwise it silently vanishes 7 days later."""
    _, job = user_and_job
    created = await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db
    )
    await patch_application(
        created.id, _STUB_USER_ID,
        PatchApplicationRequest(user_status=UserStatus.cancelled), db,
    )
    reused = await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db
    )
    assert reused.id == created.id
    assert reused.user_status == UserStatus.tracking
    days = _days_from_now(await _fetch_expires_at(db, created.id))
    assert days > 700


# ---------------------------------------------------------------------------
# Read model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_exposes_expires_at(db, user_and_job):
    _, job = user_and_job
    created = await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db
    )
    patched = await patch_application(
        created.id, _STUB_USER_ID,
        PatchApplicationRequest(user_status=UserStatus.cancelled), db,
    )
    assert patched.expires_at is not None
    assert 6.9 < _days_from_now(patched.expires_at) < 7.1
